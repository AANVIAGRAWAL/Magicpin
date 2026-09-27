import re
from typing import Dict, Any

def respond(state: Dict[str, Any], merchant_message: str) -> Dict[str, Any]:
    """
    Evaluates the conversation state and incoming message.
    Returns exactly one of:
      {action: "send", body, cta, rationale}
      {action: "wait", wait_seconds, rationale}
      {action: "end", rationale}
    """
    merchant_message = merchant_message.strip()
    merchant_message_lower = merchant_message.lower()
    
    # Initialize state fields
    if "past_merchant_messages" not in state:
        state["past_merchant_messages"] = []
    if "past_bot_bodies" not in state:
        state["past_bot_bodies"] = []
    if "auto_reply_count" not in state:
        state["auto_reply_count"] = 0
    if "hostile" not in state:
        state["hostile"] = False
    if "intent_committed" not in state:
        state["intent_committed"] = False
    if "suppress_merchant" not in state:
        state["suppress_merchant"] = False

    # 3. Hostility Handling
    # Detect abusive/hostile language (not just unrelated questions or opt-outs)
    # We require genuine abusive intent (profanity or explicit insults) rather than common opt-out words.
    hostile_patterns = [r"\b(fuck|stupid|idiots?|bitch|asshole|shut up)\b"]
    is_hostile = any(re.search(p, merchant_message_lower) for p in hostile_patterns)
    if is_hostile:
        state["hostile"] = True
        state["suppress_merchant"] = True
        # Decision: immediate end without apology to avoid provoking further hostility
        # and to gracefully fail-safe without spending Gemini tokens on a response.
        return {
            "action": "end",
            "rationale": "Hostility detected. Ending conversation and flagging for suppression."
        }

    # 1. Auto-reply detection
    def normalize_msg(m: str) -> str:
        m = m.lower().strip()
        # Remove trailing timestamps like [10:32am], (10:32 pm), etc.
        m = re.sub(r'[\[\(]?\d{1,2}:\d{2}\s*(am|pm)?[\]\)]?\s*$', '', m)
        # Strip trailing punctuation
        m = re.sub(r'[.,;!?"\'-]+$', '', m)
        # Compress multiple spaces
        m = re.sub(r'\s+', ' ', m)
        return m.strip()

    normalized_current = normalize_msg(merchant_message)
    normalized_last = normalize_msg(state["past_merchant_messages"][-1]) if state["past_merchant_messages"] else ""

    if state["past_merchant_messages"] and normalized_current == normalized_last:
        state["auto_reply_count"] += 1
        count = state["auto_reply_count"]
        
        if count == 1:
            body = "Just checking, is this an automated reply?"
            # 4. Duplicate check for canned response
            if body in state["past_bot_bodies"]:
                body = "Are you a human or a bot? I keep getting the same message."
            
            state["past_bot_bodies"].append(body)
            return {
                "action": "send",
                "body": body,
                "cta": "Reply YES",
                "rationale": "Auto-reply 1st repeat: gentle prompt"
            }
        elif count == 2:
            return {
                "action": "wait",
                "wait_seconds": 86400,
                "rationale": "Auto-reply 2nd repeat: long wait"
            }
        else:
            return {
                "action": "end",
                "rationale": "Auto-reply 3rd+ repeat: graceful end"
            }
    else:
        state["auto_reply_count"] = 0
        state["past_merchant_messages"].append(merchant_message)

    # 2. Intent transition
    # Strong phrases indicate commitment regardless of where they appear
    strong_commit_patterns = [r"\b(let'?s do it|sounds good|let'?s go|go for it|set it up)\b"]
    # Weak words only indicate commitment if they are practically the entire message (allowing punctuation)
    weak_commit_patterns = [r"^(yes|sure|ok|okay|do it|yep|yeah)[\s\.,!]*$"]
    
    is_committed = any(re.search(p, merchant_message_lower) for p in strong_commit_patterns + weak_commit_patterns)
    if is_committed:
        state["intent_committed"] = True

    # If normal reply, return action="send" with body=None.
    # This flags /v1/reply to proceed to LLM composition.
    return {
        "action": "send",
        "body": None,  
        "cta": None,
        "rationale": "Normal reply, proceed to LLM composition"
    }

def enforce_no_duplicate_body(proposed_body: str, state: Dict[str, Any]) -> str:
    """
    Called by /v1/reply after LLM composition to guarantee no duplicates.
    Forces a rewrite/skip if the exact same body text was already sent.
    """
    if proposed_body in state.get("past_bot_bodies", []):
        # Force a rewrite by tweaking the text slightly (or caller can skip)
        return proposed_body + " (Please let me know your thoughts.)"
    return proposed_body

if __name__ == "__main__":
    import pprint
    
    print("--- Running Multi-turn Handler Tests ---")
    
    # 1. Normal reply
    state1 = {}
    print("\nTest: Normal reply")
    res1 = respond(state1, "What are the details?")
    pprint.pprint(res1)
    print("State intent_committed:", state1["intent_committed"])
    
    # 2. Explicit commitment
    state2 = {}
    print("\nTest: Explicit commitment")
    res2 = respond(state2, "ok let's do it")
    pprint.pprint(res2)
    print("State intent_committed:", state2["intent_committed"])
    
    # 3. Auto-reply repeated 4 times
    state3 = {}
    print("\nTest: Auto-reply loop")
    for i in range(4):
        print(f"  Repeat {i}:")
        res = respond(state3, "I am away from my phone.")
        pprint.pprint(res)
        
    # 4. Hostile message
    state4 = {}
    print("\nTest: Hostile message")
    res4 = respond(state4, "Stop messaging me you stupid bot")
    pprint.pprint(res4)
    print("State suppress_merchant:", state4.get("suppress_merchant"))
    
    # 5. Off-topic but not hostile
    state5 = {}
    print("\nTest: Off-topic but not hostile")
    res5 = respond(state5, "Do you like pizza?")
    pprint.pprint(res5)
    print("State hostile:", state5["hostile"])
    
    # 6. Case that would duplicate prior body text
    state6 = {"past_bot_bodies": ["Just checking, is this an automated reply?"], "past_merchant_messages": ["Hello"]}
    print("\nTest: Duplicate prior body text (1st auto-reply repeat but standard prompt already used)")
    res6 = respond(state6, "Hello")
    pprint.pprint(res6)
    
    # 7. False-positive 'stop' test
    state7 = {}
    print("\nTest: False-positive 'stop' (should NOT be hostile)")
    res7 = respond(state7, "please stop texting me at night")
    pprint.pprint(res7)
    print("State hostile:", state7["hostile"])
    
    # 8. False-positive 'spam' test
    state8 = {}
    print("\nTest: False-positive 'spam' (should NOT be hostile)")
    res8 = respond(state8, "is this spam or real?")
    pprint.pprint(res8)
    print("State hostile:", state8["hostile"])
    
    # 9. False-positive commitment
    state9 = {}
    print("\nTest: False-positive 'Ok' (should NOT commit)")
    res9 = respond(state9, "Ok, what's the price?")
    pprint.pprint(res9)
    print("State intent_committed:", state9["intent_committed"])
    
    # 10. True-positive commitment
    state10 = {}
    print("\nTest: True-positive commitment")
    res10 = respond(state10, "Yes, let's do it")
    pprint.pprint(res10)
    print("State intent_committed:", state10["intent_committed"])
    
    # 11. True-positive hostility test
    state11 = {}
    print("\nTest: True-positive hostility (stop + idiot)")
    res11 = respond(state11, "stop spamming me, you idiots")
    pprint.pprint(res11)
    print("State hostile:", state11.get("hostile"))
    
    # 12. Auto-reply normalization test
    state12 = {}
    print("\nTest: Auto-reply normalization with trailing timestamps")
    # First message
    res12_a = respond(state12, "We're currently unavailable. [10:32am]")
    print(f"  First message count: {state12['auto_reply_count']} (Expected: 0)")
    # Second message with different timestamp
    res12_b = respond(state12, "We're currently unavailable. [10:47am]")
    pprint.pprint(res12_b)
    print(f"  Second message count: {state12['auto_reply_count']} (Expected: 1)")
