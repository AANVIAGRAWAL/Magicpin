import asyncio
from fastapi.testclient import TestClient
from bot import app, context_store, conversation_store, suppression_store, response_cache

client = TestClient(app)

def setup_contexts():
    # Setup Category
    client.post("/v1/context", json={
        "scope": "category",
        "context_id": "dentists",
        "version": 1,
        "payload": {
            "slug": "dentists",
            "voice": {"tone": "clinical"},
            "peer_stats": {},
            "digest": [{"title": "Test digest"}]
        }
    })
    
    # Setup Merchant
    client.post("/v1/context", json={
        "scope": "merchant",
        "context_id": "m_test_123",
        "version": 1,
        "payload": {
            "merchant_id": "m_test_123",
            "category_slug": "dentists",
            "name": "Dr. Meera's Clinic"
        }
    })

def test_tick():
    print("\n=== Test 1: /v1/tick (Simulated Time Expiry) ===")
    
    # User's scenario:
    # 1. Real-world time is 2026-09-27.
    # 2. expires_at is 2026-04-20T00:00:00Z.
    # 3. Simulated "now" is 2026-04-26T10:35:00Z.
    # The trigger is EXPIRED per the simulated clock (04-20 < 04-26).
    
    m_id = "m_test_expiry"
    client.post("/v1/context", json={
        "scope": "merchant",
        "context_id": m_id,
        "version": 1,
        "payload": {
            "merchant_id": m_id,
            "category_slug": "dentists",
            "name": "Dr. Expiry Clinic"
        }
    })
    
    t_id = "t_expire_test"
    client.post("/v1/context", json={
        "scope": "trigger",
        "context_id": t_id,
        "version": 1,
        "payload": {
            "id": t_id,
            "merchant_id": m_id,
            "customer_id": "c_expiry",
            "kind": "recall_due",
            "urgency": 5,
            "expires_at": "2026-04-20T00:00:00Z",
            "payload": {"service_due": "6 month cleaning", "due_date": "2026-09-15"}
        }
    })
    
    payload = {
        "now": "2026-04-26T10:35:00Z",
        "available_triggers": [t_id]
    }
    
    print(f"Trigger expires_at: 2026-04-20T00:00:00Z")
    print(f"Request now:        {payload['now']}")
    
    response = client.post("/v1/tick", json=payload)
    data = response.json()
    actions = data.get("actions", [])
    
    print(f"Status Code: {response.status_code}")
    print(f"Number of actions returned: {len(actions)}")
    if not actions:
        print("SUCCESS: Trigger was correctly rejected as EXPIRED based on the judge's simulated clock!")
    else:
        print("FAILURE: Trigger was processed! Expiry filtering failed.")

def test_auto_reply():
    print("\n=== Test 2: /v1/reply (Auto-Reply Escalation) ===")
    for i in range(4):
        print(f"\n--- Turn {i+1} ---")
        payload = {
            "merchant_id": "m_test_123",
            "conversation_id": "c_2",
            "message": "We are currently closed. Please leave a message."
        }
        response = client.post("/v1/reply", json=payload)
        print(f"Status Code: {response.status_code}")
        print("Response:")
        import pprint
        pprint.pprint(response.json())
        
def test_intent():
    print("\n=== Test 3: /v1/reply (Intent Commitment) ===")
    payload = {
        "merchant_id": "m_test_123",
        "conversation_id": "c_3",
        "message": "Yes, let's do it"
    }
    response = client.post("/v1/reply", json=payload)
    print(f"Status Code: {response.status_code}")
    print("Response:")
    import pprint
    pprint.pprint(response.json())

if __name__ == "__main__":
    client.post("/v1/teardown")
    setup_contexts()
    test_tick()
