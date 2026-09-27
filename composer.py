import os
import json
import hashlib
from typing import Dict, Any, Optional
from decision_layer import calculate_cache_key
from datetime import datetime
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

try:
    client = genai.Client(http_options={'timeout': 8000})
except Exception:
    client = None

def generate_fallback_message(contexts: Dict[str, Any], trigger_id: str) -> Dict[str, Any]:
    """Generates a safe fallback message grounded only in provided facts when Gemini fails."""
    trigger = contexts.get("trigger", {})
    merchant = contexts.get("merchant", {})
    customer = contexts.get("customer") or {}
    
    merchant_name = merchant.get("identity", {}).get("name", "our clinic/store")
    customer_name = customer.get("identity", {}).get("name", "Customer")
    
    body = f"Hi {customer_name}, this is an update from {merchant_name}. Please let us know how we can help you."
    
    trigger_kind = trigger.get("kind")
    trigger_payload = trigger.get("payload", {})
    
    if trigger_kind == "recall_due":
        service = trigger_payload.get("service_due", "scheduled visit")
        service = service.replace("_", " ") if service else "scheduled visit"
        
        due_date_str = trigger_payload.get("due_date", "")
        date_formatted = ""
        if due_date_str:
            try:
                dt = datetime.fromisoformat(due_date_str)
                date_formatted = f" on {dt.strftime('%B %-d')}"
            except ValueError:
                date_formatted = f" on {due_date_str}"
                
        body = f"Hi {customer_name}, {merchant_name} here. It's time for your {service}{date_formatted}."
    elif trigger_kind == "ipl_match_today":
        body = f"Hi {customer_name}, {merchant_name} is ready for the match today! Let us know if you'd like to place an order."
    elif trigger_kind == "regulation_change":
        body = f"Hi {merchant_name}, there's an important regulation change affecting your clinic/store. Let me know if you need details."
    elif trigger_kind == "perf_dip":
        body = f"Hi {merchant_name}, I noticed a slight dip in performance this week. Would you like to run a quick campaign to boost numbers?"
    elif trigger_kind == "renewal_due":
        body = f"Hi {merchant_name}, your subscription is due for renewal soon. Should I help you process it?"
    elif trigger_kind == "winback_eligible" or trigger_kind == "customer_lapsed_hard":
        body = f"Hi {customer_name}, we've missed you at {merchant_name}! Reply to see our special offer for you."
    elif trigger_kind == "supply_alert":
        body = f"URGENT: {merchant_name}, we have received a supply alert regarding your inventory. Please check."
    elif trigger_kind == "cde_opportunity":
        body = f"Hi {merchant_name}, there is a new educational opportunity available for your team."
    else:
        body = f"Hi {customer_name}, this is an update from {merchant_name}. Please let us know how we can help you."
        
    return {
        "body": body,
        "cta": "Reply YES for more info",
        "suppression_key": trigger.get("suppression_key", f"fallback_{trigger_id}"),
        "rationale": "Generated via rule-based fallback due to LLM failure/quota."
    }

def compose_message(
    merchant_id: str,
    trigger_id: str,
    contexts: Dict[str, Any],
    past_bot_bodies: list[str],
    response_cache: Dict[str, Any],
    intent_committed: bool = False
) -> Dict[str, Any]:
    """
    Composes a message using Gemini 3.6 Flash.
    Returns {body, cta, suppression_key, rationale}.
    """
    # 6. Check response cache first
    contexts_for_cache = dict(contexts)
    if intent_committed:
        contexts_for_cache["_internal_intent_committed"] = True
    cache_key = calculate_cache_key(merchant_id, trigger_id, contexts_for_cache)
    
    if cache_key in response_cache:
        return response_cache[cache_key]
        
    if not client:
        print("Gemini client not initialized. Returning rule-based template without caching.")
        return generate_fallback_message(contexts, trigger_id)
        
    system_instruction = """You are 'Vera', an expert WhatsApp engagement composer for merchants.
Your goal is to write a highly compelling, short, context-aware WhatsApp message on behalf of the merchant.

You will be given the following contexts as JSON: CategoryContext, MerchantContext, TriggerContext, CustomerContext.

RUBRIC & SCORING DIMENSIONS (Maximize these):
1. Specificity: Root the message in exact facts provided in the payload (dates, names, metrics). NO generics.
2. Category Fit: Strictly adhere to the CategoryContext's tone, voice, and taboos. Never invent per-vertical logic.
3. Merchant Fit: Match the merchant's identity and past engagement style.
4. Decision Quality: Address the trigger's core urgency clearly.
5. Engagement Compulsion: Pick EXACTLY ONE of the 8 engagement levers (Curiosity, Urgency, Exclusivity, Status, Utility/Value, Reciprocity, Social Proof, FOMO).

ANTI-PATTERNS (CRITICAL PENALTIES - AVOID THESE):
- NEVER invent facts, prices, discounts, citations, or numbers not present in the payload.
- Generate EXACTLY ONE clear Call-to-Action (CTA) phrase.
- NEVER include URLs or links in the message body.
- NEVER use a "hype" or "salesy" tone if forbidden by Category taboos.
- NEVER repeat prior body text.

CHAIN OF THOUGHT:
Before writing the message, silently pick one Engagement Compulsion lever that best fits the trigger, and plan how to use it.

OUTPUT FORMAT:
Return a JSON object matching this schema:
{
  "lever_chosen": "string (the compulsion lever)",
  "rationale": "string (brief explanation of how the message maximizes the rubric)",
  "body": "string (the main WhatsApp message body, under 40 words)",
  "cta": "string (a short, distinct CTA, e.g., 'Reply YES to confirm')",
  "suppression_key": "string (copied from the TriggerContext to prevent duplicate sends)"
}

FEW-SHOT EXAMPLE (Conceptual):
Trigger: A salon client hasn't visited in 40 days.
Lever chosen: Reciprocity
Body: "Hi Sarah! We loved having you at Studio 11 last month. Your usual stylist Priya has a rare opening this Thursday evening."
CTA: "Reply BOOK for Thursday"
"""
    
    if intent_committed:
        system_instruction += "\n\nCRITICAL: The user has explicitly committed. DO NOT ask any more qualifying questions. You MUST move immediately to concrete action (e.g. asking for preferred dates/times, sending a link, confirming details)."
    
    prompt = f"""Here is the context payload:
{json.dumps(contexts, indent=2)}

Past Bot Bodies (do not repeat these):
{json.dumps(past_bot_bodies, indent=2)}

Please generate the message according to the system instructions.
"""
    
    try:
        response_schema = {
            "type": "OBJECT",
            "properties": {
                "lever_chosen": {"type": "STRING"},
                "rationale": {"type": "STRING"},
                "body": {"type": "STRING"},
                "cta": {"type": "STRING"},
                "suppression_key": {"type": "STRING"}
            },
            "required": ["lever_chosen", "rationale", "body", "cta", "suppression_key"]
        }

        # First attempt with thinking_level="none"
        try:
            interaction = client.interactions.create(
                model="gemini-3.6-flash",
                input=prompt,
                system_instruction=system_instruction,
                generation_config={
                    "temperature": 0.0,
                    "response_mime_type": "application/json",
                    "response_schema": response_schema,
                    "thinking_level": "none"
                }
            )
            print("DEBUG: API call succeeded with thinking_level='none'")
        except Exception as e:
            if "thinking_level" in str(e).lower() or "invalid value" in str(e).lower() or "unsupported" in str(e).lower():
                # Fall back to minimal thinking budget
                print(f"DEBUG: 'none' rejected: {e}. Trying 'minimal'.")
                try:
                    interaction = client.interactions.create(
                        model="gemini-3.6-flash",
                        input=prompt,
                        system_instruction=system_instruction,
                        generation_config={
                            "temperature": 0.0,
                            "response_mime_type": "application/json",
                            "response_schema": response_schema,
                            "thinking_level": "minimal"
                        }
                    )
                except Exception as e2:
                    print(f"DEBUG: 'minimal' rejected: {e2}. Trying 'low'.")
                    interaction = client.interactions.create(
                        model="gemini-3.6-flash",
                        input=prompt,
                        system_instruction=system_instruction,
                        generation_config={
                            "temperature": 0.0,
                            "response_mime_type": "application/json",
                            "response_schema": response_schema,
                            "thinking_level": "low"
                        }
                    )
            else:
                raise e
                
        output_text = interaction.output_text or ""
        # Strip markdown formatting if present
        if output_text.startswith("```json"):
            output_text = output_text[7:-3].strip()
        elif output_text.startswith("```"):
            output_text = output_text[3:-3].strip()
            
        try:
            result_json = json.loads(output_text)
        except Exception as json_err:
            print(f"Failed to parse JSON. Raw output: {output_text!r}")
            raise json_err
            
        final_result = {
            "body": result_json.get("body", ""),
            "cta": result_json.get("cta", ""),
            "suppression_key": result_json.get("suppression_key", ""),
            "rationale": f"Lever: {result_json.get('lever_chosen')}. {result_json.get('rationale')}"
        }
        
        # 6. Cache the successful result
        response_cache[cache_key] = final_result
        return final_result
        
    except Exception as e:
        print(f"Gemini API call failed: {e}. Returning rule-based template without caching.")
        return generate_fallback_message(contexts, trigger_id)

if __name__ == "__main__":
    import pprint
    
    print("--- Running Determinism Test ---")
    
    test_contexts = {
        "category": {
            "name": "dentists",
            "voice": "Clinical, reassuring, professional",
            "taboos": ["hype", "salesy", "emojis"]
        },
        "merchant": {
            "id": "m_001_drmeera",
            "payload": {"name": "Dr. Meera's Dental Clinic"}
        },
        "trigger": {
            "id": "t_recall_001",
            "kind": "recall_due",
            "suppression_key": "recall_m001_c001",
            "payload": {"due_date": "2026-11-12", "service_due": "6_month_cleaning"}
        },
        "customer": {
            "id": "c_001",
            "payload": {"name": "Priya", "last_service_date": "2026-05-12"}
        }
    }
    
    # Pass empty caches to force two distinct LLM calls instead of returning the cached result
    res1 = compose_message("m_001", "t_recall_001", test_contexts, [], {})
    res2 = compose_message("m_001", "t_recall_001", test_contexts, [], {})
    
    print("\n--- Call 1 Output ---")
    pprint.pprint(res1)
    
    print("\n--- Call 2 Output ---")
    pprint.pprint(res2)
    
    if res1 == res2:
        print("\nSUCCESS: Both calls produced IDENTICAL outputs, proving maximum determinism.")
    else:
        print("\nWARNING: Outputs differ. Determinism is not 100%.")
