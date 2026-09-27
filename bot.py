import os
from typing import Dict, List, Any, Optional
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
import itertools
import asyncio

from decision_layer import evaluate_triggers
from composer import compose_message, generate_fallback_message
from conversation_handlers import respond, enforce_no_duplicate_body

load_dotenv()

_global_sem = None
def get_semaphore():
    global _global_sem
    if _global_sem is None:
        _global_sem = asyncio.Semaphore(5)
    return _global_sem
    
app = FastAPI(title="Vera - magicpin AI Challenge")

# --- Configuration & API Keys ---
# Simple rotation-ready structure for Gemini API keys
def load_api_keys() -> List[str]:
    keys = []
    # Try reading multiple keys for rotation
    for i in range(1, 4):
        key = os.getenv(f"GEMINI_API_KEY_{i}")
        if key:
            keys.append(key)
    # Fallback to standard key if numbered keys are not found
    if not keys:
        default_key = os.getenv("GEMINI_API_KEY")
        if default_key:
            keys.append(default_key)
    return keys

GEMINI_API_KEYS = load_api_keys()
key_iterator = itertools.cycle(GEMINI_API_KEYS) if GEMINI_API_KEYS else None

def get_next_api_key() -> Optional[str]:
    """Retrieves the next Gemini API key in the rotation."""
    if key_iterator:
        return next(key_iterator)
    return None

# --- In-Memory Stores ---

# 1. Context Store
# Key: (scope: str, context_id: str)
# Value: {"version": int, "payload": dict}
context_store: Dict[tuple[str, str], dict] = {}

# 2. Conversation Store
# Key: conversation_id: str
# Value: {"turns": list, "auto_reply_count": int, "hostile": bool, "intent_committed": bool}
conversation_store: Dict[str, dict] = {}

# 3. Suppression/Dedup Store
# Key: suppression_key: str
# Value: True (or timestamp if expiry tracking is needed)
suppression_store: Dict[str, bool] = {}

# 4. Response Cache
# Key: string (hash of merchant_id, trigger_id, and the 4 input contexts)
# Value: last generated message payload dict
response_cache: Dict[str, dict] = {}

# --- Endpoints (Skeletons) ---

MAX_PAYLOAD_SIZE = 500 * 1024  # 500 KB

@app.post("/v1/context")
async def receive_context(request: Request):
    """Receive category/merchant/customer/trigger context pushes."""
    body_bytes = await request.body()
    if len(body_bytes) > MAX_PAYLOAD_SIZE:
        return JSONResponse(
            status_code=400, 
            content={"error": "PayloadTooLarge", "message": "Payload exceeds 500 KB limit."}
        )
    
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(
            status_code=400, 
            content={"error": "InvalidJSON", "message": "Malformed JSON payload."}
        )
        
    scope = data.get("scope")
    context_id = data.get("context_id")
    version = data.get("version")
    payload = data.get("payload")
    
    if not isinstance(scope, str) or not isinstance(context_id, str) or not isinstance(version, int) or not isinstance(payload, dict):
        return JSONResponse(
            status_code=400, 
            content={
                "error": "ValidationError", 
                "message": "Missing required fields or invalid types. Required: scope (str), context_id (str), version (int), payload (dict)."
            }
        )
        
    if scope not in ["category", "merchant", "trigger", "customer"]:
        return JSONResponse(
            status_code=400, 
            content={
                "error": "InvalidScope", 
                "message": "Scope must be one of: category, merchant, trigger, customer."
            }
        )
        
    key = (scope, context_id)
    
    if key in context_store:
        stored = context_store[key]
        if version == stored["version"] and payload == stored["payload"]:
            return {"accepted": True, "status": "success", "message": "Context unchanged (no-op)."}
        if version > stored["version"]:
            context_store[key] = {"version": version, "payload": payload}
            return {"accepted": True, "status": "success", "message": "Context updated to new version."}
            
        return JSONResponse(
            status_code=409, 
            content={
                "error": "VersionConflict", 
                "message": f"Version {version} is older than or equal to stored version {stored['version']}, but payload differs."
            }
        )
        
    context_store[key] = {"version": version, "payload": payload}
    return {"accepted": True, "status": "success", "message": "Context created."}

@app.post("/v1/teardown")
async def handle_teardown():
    """Wipes all context and state stores."""
    context_store.clear()
    conversation_store.clear()
    suppression_store.clear()
    response_cache.clear()
    return {"status": "success", "message": "All stores cleared."}

@app.post("/v1/tick")
async def handle_tick(request: Request):
    """Periodic wake-up. Bot inspects current context and returns proactive actions."""
    data = await request.json()
    available_trigger_ids = data.get("available_triggers", [])
    now_str = data.get("now")
    
    from datetime import datetime
    current_time = None
    if now_str:
        try:
            if now_str.endswith('Z'):
                now_str = now_str[:-1] + '+00:00'
            current_time = datetime.fromisoformat(now_str)
        except ValueError:
            pass
            
    # Group triggers by merchant_id
    merchant_triggers = {}
    for t_id in available_trigger_ids:
        trigger_payload = context_store.get(("trigger", t_id), {}).get("payload")
        if not trigger_payload:
            continue
        m_id = trigger_payload.get("merchant_id")
        if not m_id:
            continue
        merchant_triggers.setdefault(m_id, []).append(trigger_payload)
        
    sem = get_semaphore()
    in_flight_count = 0
    import time
        
    async def process_merchant(merchant_id, triggers):
        nonlocal in_flight_count
        print(f"[{time.strftime('%H:%M:%S.%f')[:-3]}] Merchant {merchant_id} waiting for semaphore...")
        async with sem:
            in_flight_count += 1
            print(f"[{time.strftime('%H:%M:%S.%f')[:-3]}] Merchant {merchant_id} ACQUIRED semaphore. In-flight: {in_flight_count}")
            merchant_ctx = context_store.get(("merchant", merchant_id), {}).get("payload", {})
            category_slug = merchant_ctx.get("category_slug", "dentists")
            category_ctx = context_store.get(("category", category_slug), {}).get("payload", {})
        
        base_contexts = {"category": category_ctx, "merchant": merchant_ctx}
        
        # Evaluate triggers
        decision = evaluate_triggers(
            merchant_id=merchant_id,
            conversation_id="", # We don't have it yet, evaluate_triggers doesn't strictly need it
            triggers=triggers,
            suppression_store=suppression_store,
            response_cache=response_cache,
            contexts=base_contexts,
            current_time=current_time
        )
        
        if not decision.get("trigger_id"):
            in_flight_count -= 1
            print(f"[{time.strftime('%H:%M:%S.%f')[:-3]}] Merchant {merchant_id} RELEASED semaphore (no trigger). In-flight: {in_flight_count}")
            return None
            
        trigger_id = decision["trigger_id"]
        best_contexts = decision["best_contexts"]
        customer_id = best_contexts["trigger"].get("customer_id")
        
        # Determine a deterministic conversation_id
        conversation_id = f"conv_{merchant_id}_{customer_id}" if customer_id else f"conv_{merchant_id}_{trigger_id}"
        
        conv_state = conversation_store.setdefault(conversation_id, {
            "turns": [],
            "auto_reply_count": 0,
            "hostile": False,
            "intent_committed": False,
            "past_merchant_messages": [],
            "past_bot_bodies": [],
            "suppress_merchant": False
        })
        
        if conv_state.get("hostile") or conv_state.get("suppress_merchant"):
            in_flight_count -= 1
            print(f"[{time.strftime('%H:%M:%S.%f')[:-3]}] Merchant {merchant_id} RELEASED semaphore (suppressed). In-flight: {in_flight_count}")
            return None
            
        send_as = "merchant_on_behalf" if customer_id else "vera"
        
        if decision.get("use_cache") and decision.get("cached_payload"):
            message_payload = decision["cached_payload"]
        else:
            try:
                # 25s timeout per composer call
                message_payload = await asyncio.wait_for(
                    asyncio.to_thread(
                        compose_message,
                        merchant_id=merchant_id,
                        trigger_id=trigger_id,
                        contexts=best_contexts,
                        past_bot_bodies=conv_state["past_bot_bodies"],
                        response_cache=response_cache
                    ),
                    timeout=12.0
                )
            except asyncio.TimeoutError:
                in_flight_count -= 1
                print(f"[{time.strftime('%H:%M:%S.%f')[:-3]}] Merchant {merchant_id} RELEASED semaphore (timeout). In-flight: {in_flight_count}")
                message_payload = generate_fallback_message(best_contexts, trigger_id)
            except Exception as e:
                print(f"Error composing: {e}")
                in_flight_count -= 1
                print(f"[{time.strftime('%H:%M:%S.%f')[:-3]}] Merchant {merchant_id} RELEASED semaphore (error). In-flight: {in_flight_count}")
                message_payload = generate_fallback_message(best_contexts, trigger_id)
                
        in_flight_count -= 1
        print(f"[{time.strftime('%H:%M:%S.%f')[:-3]}] Merchant {merchant_id} RELEASED semaphore (success). In-flight: {in_flight_count}")
                
        # Write to suppression store
        suppression_key = message_payload.get("suppression_key")
        if suppression_key:
            suppression_store[suppression_key] = True
            
        body = message_payload.get("body", "")
        if body:
            conv_state["past_bot_bodies"].append(body)
            
        merchant_name = merchant_ctx.get("identity", {}).get("name", "Merchant")
        return {
            "conversation_id": conversation_id,
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": send_as,
            "trigger_id": trigger_id,
            "template_name": "vera_initial_contact",
            "template_params": {"1": merchant_name},
            "body": body,
            "cta": message_payload.get("cta"),
            "suppression_key": suppression_key,
            "rationale": f"L1: {decision.get('rationale')} | L2: {message_payload.get('rationale')}"
        }

    # Process all merchants concurrently
    tasks = [process_merchant(m_id, trgs) for m_id, trgs in merchant_triggers.items()]
    
    actions = []
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for res in results:
            if isinstance(res, dict):
                actions.append(res)
                
    return {"actions": actions}


@app.post("/v1/reply")
async def handle_reply(request: Request):
    """Synchronous handler for an incoming merchant/customer reply."""
    data = await request.json()
    merchant_id = data.get("merchant_id")
    conversation_id = data.get("conversation_id")
    merchant_message = data.get("message", "")
    
    print(f"[REPLY] merchant_id={merchant_id} conv_id={conversation_id} length={len(merchant_message)}")
    
    if not merchant_id or not conversation_id or not merchant_message:
        return JSONResponse(status_code=400, content={"error": "Missing required fields."})
        
    # Global state checks for the conversation
    conv_state = conversation_store.setdefault(conversation_id, {
        "turns": [],
        "auto_reply_count": 0,
        "hostile": False,
        "intent_committed": False,
        "past_merchant_messages": [],
        "past_bot_bodies": [],
        "suppress_merchant": False
    })
    
    # Use our Layer 1 conversation handlers
    handler_result = respond(conv_state, merchant_message)
    
    # Update global suppression if hostility flagged
    if conv_state.get("suppress_merchant"):
        suppression_store[f"merchant_{merchant_id}_suppressed"] = True
        
    if handler_result.get("action") == "end":
        return handler_result
        
    if handler_result.get("action") == "wait":
        return handler_result
        
    # If action is 'send' but body is None, it means proceed to L2 composition
    if handler_result.get("action") == "send" and not handler_result.get("body"):
        merchant_ctx = context_store.get(("merchant", merchant_id), {}).get("payload", {})
        category_slug = merchant_ctx.get("category_slug", "dentists")
        category_ctx = context_store.get(("category", category_slug), {}).get("payload", {})
        
        # Craft an active reply trigger context
        dummy_trigger = {
            "id": "trg_reply_active",
            "kind": "conversation_reply",
            "payload": {}
        }
        best_contexts = {
            "category": category_ctx,
            "merchant": merchant_ctx,
            "trigger": dummy_trigger
        }
        
        try:
            msg = await asyncio.wait_for(
                asyncio.to_thread(
                    compose_message,
                    merchant_id=merchant_id,
                    trigger_id="trg_reply_active",
                    contexts=best_contexts,
                    past_bot_bodies=conv_state["past_bot_bodies"],
                    response_cache=response_cache,
                    intent_committed=conv_state.get("intent_committed", False)
                ),
                timeout=12.0
            )
        except asyncio.TimeoutError:
            print("[REPLY] Warning: Timeout risk approaching 30s. Bailing out gracefully.")
            fallback = generate_fallback_message(best_contexts, "trg_reply_active")
            msg = {"body": fallback["body"], "cta": fallback["cta"], "rationale": "Timeout during composition (fallback)."}
            
        proposed_body = msg.get("body", "I understand. I will proceed with this request.")
        final_body = enforce_no_duplicate_body(proposed_body, conv_state)
        
        conv_state["past_bot_bodies"].append(final_body)
        
        return {
            "action": "send",
            "body": final_body,
            "cta": msg.get("cta"),
            "rationale": f"Handler: {handler_result.get('rationale')} | L2: {msg.get('rationale')}"
        }
        
    return handler_result

@app.get("/v1/healthz")
async def healthz():
    """Liveness probe + loaded-context counts."""
    counts = {
        "category": 0,
        "merchant": 0,
        "trigger": 0,
        "customer": 0
    }
    
    # Safely iterate and count contexts by scope
    for scope, _ in context_store.keys():
        if scope in counts:
            counts[scope] += 1
            
    return {
        "status": "ok",
        "contexts_loaded": counts
    }

@app.get("/v1/metadata")
async def metadata():
    """Team name, members, model, approach, contact email, version."""
    return {
        "team_name": "YourActualTeamName",
        "members": "Your Actual Name(s)",
        "contact_email": "your.actual@email.com",
        "model": "gemini-3.6-flash",
        "approach": "Rule-based decision layer + Gemini Flash single-shot composer",
        "version": "1.0.0"
    }
