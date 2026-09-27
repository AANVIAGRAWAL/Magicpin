import asyncio
import time
from bot import context_store, evaluate_triggers, suppression_store, response_cache, get_semaphore

async def test_concurrency():
    sem = get_semaphore()
    in_flight_count = 0
    
    # Mock compose_message
    async def mock_compose(*args, **kwargs):
        await asyncio.sleep(2.0)
        return {"body": "Mock response", "rationale": "Mock rationale"}
    
    async def process_merchant(merchant_id, triggers):
        nonlocal in_flight_count
        print(f"[{time.strftime('%H:%M:%S.%f')[:-3]}] Merchant {merchant_id} waiting for semaphore...")
        async with sem:
            in_flight_count += 1
            print(f"[{time.strftime('%H:%M:%S.%f')[:-3]}] Merchant {merchant_id} ACQUIRED semaphore. In-flight: {in_flight_count}")
            
            best_contexts = {"trigger": {"payload": {"customer_id": "c_1"}}}
            # Simulate wait_for + to_thread
            await asyncio.wait_for(mock_compose(), timeout=25.0)
            
            in_flight_count -= 1
            print(f"[{time.strftime('%H:%M:%S.%f')[:-3]}] Merchant {merchant_id} RELEASED semaphore. In-flight: {in_flight_count}")
            return {"status": "success"}

    tasks = []
    print("--- Sending request with 6 triggers (concurrency cap is 5) ---")
    for i in range(6):
        tasks.append(process_merchant(f"m_test_{i}", []))
        
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(test_concurrency())
