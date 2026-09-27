import hashlib
import json
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone, timedelta

def calculate_cache_key(merchant_id: str, trigger_id: str, contexts: Dict[str, Any]) -> str:
    """Calculates a deterministic hash for the cache key."""
    # Ensure stable json dump for identical contexts
    contexts_str = json.dumps(contexts, sort_keys=True)
    raw = f"{merchant_id}|{trigger_id}|{contexts_str}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

def evaluate_triggers(
    merchant_id: str,
    conversation_id: str,
    triggers: List[Dict[str, Any]],
    suppression_store: Dict[str, Any],
    response_cache: Dict[str, Any],
    contexts: Dict[str, Any],
    current_time: Optional[datetime] = None
) -> Dict[str, Any]:
    """
    Evaluates available triggers and picks the best one to act on.
    Returns a decision object with trigger_id, use_cache, cached_payload, rationale, and best_contexts.
    """
    if current_time is None:
        current_time = datetime.now(timezone.utc)
        
    valid_triggers = []
    
    for t in triggers:
        trigger_id = t.get("id")
        
        # 1. Filter out expired triggers
        expires_at_str = t.get("expires_at")
        if expires_at_str:
            try:
                # Handle 'Z' suffix for older python versions
                if expires_at_str.endswith('Z'):
                    expires_at_str = expires_at_str[:-1] + '+00:00'
                expires_at = datetime.fromisoformat(expires_at_str)
                if expires_at < current_time:
                    print(f"Trigger {trigger_id} expired! {expires_at} < {current_time}")
                    continue  # Expired
                print(f"Trigger {trigger_id} valid! {expires_at} >= {current_time}")
            except ValueError:
                pass # Ignore malformed dates
                
        # 2. Filter out suppressed triggers
        suppression_key = t.get("suppression_key")
        if suppression_key and suppression_key in suppression_store:
            print(f"Trigger {trigger_id} suppressed! {suppression_key}")
            continue # Suppressed
            
        # Only consider triggers with urgency > 0
        urgency = t.get("urgency", 0)
        if urgency > 0:
            valid_triggers.append(t)
            
    # 3. If no valid triggers, return empty decision
    if not valid_triggers:
        return {
            "trigger_id": None,
            "use_cache": False,
            "cached_payload": None,
            "rationale": "No valid, unsuppressed, or unexpired triggers with urgency > 0 found."
        }
        
    # Sort by urgency descending (pick the best one)
    valid_triggers.sort(key=lambda x: x.get("urgency", 0), reverse=True)
    best_trigger = valid_triggers[0]
    best_trigger_id = best_trigger.get("id")
    
    # 4. Check response cache before calling LLM
    best_contexts = dict(contexts)
    best_contexts["trigger"] = best_trigger
    cache_key = calculate_cache_key(merchant_id, best_trigger_id, best_contexts)
    
    if cache_key in response_cache:
        return {
            "trigger_id": best_trigger_id,
            "use_cache": True,
            "cached_payload": response_cache[cache_key],
            "rationale": f"Trigger '{best_trigger_id}' selected (urgency {best_trigger.get('urgency')}). Found identical context in cache; reusing previous message.",
            "best_contexts": best_contexts
        }
        
    # 5. Return decision to call LLM
    return {
        "trigger_id": best_trigger_id,
        "use_cache": False,
        "cached_payload": None,
        "rationale": f"Trigger '{best_trigger_id}' selected (urgency {best_trigger.get('urgency')}) as the highest priority candidate.",
        "best_contexts": best_contexts
    }

if __name__ == "__main__":
    import pprint
    
    print("--- Running Layer 1 Tests ---")
    now = datetime.now(timezone.utc)
    past = now - timedelta(days=1)
    future = now + timedelta(days=1)
    
    m_id = "m_test_123"
    c_id = "conv_abc"
    contexts = {"category": {"theme": "test"}, "merchant": {"name": "test clinic"}}
    
    suppression_store = {"suppress_me": True}
    
    # Pre-populate cache for the 'cached_trigger'
    cached_trigger_id = "t_cached"
    ckey = calculate_cache_key(m_id, cached_trigger_id, contexts)
    response_cache = {
        ckey: {"body": "This is a cached message.", "cta": "Reply OK"}
    }
    
    # Test Triggers
    triggers = [
        # 1. Expired
        {"id": "t_expired", "urgency": 5, "expires_at": past.isoformat(), "suppression_key": "k1"},
        # 2. Suppressed
        {"id": "t_suppressed", "urgency": 5, "expires_at": future.isoformat(), "suppression_key": "suppress_me"},
        # 3. Valid (Low urgency)
        {"id": "t_low", "urgency": 2, "expires_at": future.isoformat(), "suppression_key": "k3"},
        # 4. Valid (High urgency) - Should win
        {"id": "t_high", "urgency": 4, "expires_at": future.isoformat(), "suppression_key": "k4"},
        # 5. Valid (Cached) - Urgency 3
        {"id": "t_cached", "urgency": 3, "expires_at": future.isoformat(), "suppression_key": "k5"},
    ]
    
    print("\nTest 1: Normal evaluation (filters expired/suppressed, picks highest urgency 't_high')")
    dec1 = evaluate_triggers(m_id, c_id, triggers, suppression_store, response_cache, contexts, current_time=now)
    pprint.pprint(dec1)
    
    print("\nTest 2: Cache Hit (if we only pass the cached trigger)")
    dec2 = evaluate_triggers(m_id, c_id, [triggers[4]], suppression_store, response_cache, contexts, current_time=now)
    pprint.pprint(dec2)
    
    print("\nTest 3: No good candidate (all expired or suppressed)")
    bad_triggers = [triggers[0], triggers[1]]
    dec3 = evaluate_triggers(m_id, c_id, bad_triggers, suppression_store, response_cache, contexts, current_time=now)
    pprint.pprint(dec3)
    
    print("\nTest 4: Empty triggers array")
    dec4 = evaluate_triggers(m_id, c_id, [], suppression_store, response_cache, contexts, current_time=now)
    pprint.pprint(dec4)
    
    print("\nTest 5: High urgency without cache vs Lower urgency with cache")
    triggers_test5 = [
        {"id": "t_high_no_cache", "urgency": 4, "expires_at": future.isoformat(), "suppression_key": "k6"},
        {"id": "t_low_with_cache", "urgency": 2, "expires_at": future.isoformat(), "suppression_key": "k7"}
    ]
    ckey2 = calculate_cache_key(m_id, "t_low_with_cache", contexts)
    response_cache[ckey2] = {"body": "Cached message for low urgency.", "cta": "OK"}
    
    dec5 = evaluate_triggers(m_id, c_id, triggers_test5, suppression_store, response_cache, contexts, current_time=now)
    pprint.pprint(dec5)
