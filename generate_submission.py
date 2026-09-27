import json
import os
import requests
import time
from pathlib import Path

def load_dataset():
    base = Path("dataset/expanded")
    categories = {}
    for f in (base / "categories").glob("*.json"):
        categories[f.stem] = json.load(open(f))
        
    def load_items(name):
        res = {}
        for f in (base / name).glob("*.json"):
            item = json.load(open(f))
            key_field = "merchant_id" if name == "merchants" else "customer_id" if name == "customers" else "id"
            res[item[key_field]] = item
        return res
        
    merchants = load_items("merchants")
    customers = load_items("customers")
    triggers = load_items("triggers")
    
    pairs = json.load(open(base / "test_pairs.json"))["pairs"]
    return categories, merchants, customers, triggers, pairs

def generate_submission():
    categories, merchants, customers, triggers, pairs = load_dataset()
    base_url = "http://localhost:8080"
    
    # 1. Reset bot via teardown if possible
    try:
        requests.post(f"{base_url}/v1/teardown", timeout=2)
    except:
        pass
        
    submission_lines = []
    
    # Pre-push all contexts just like the judge does
    print("Pushing contexts...")
    for cat in categories.values():
        requests.post(f"{base_url}/v1/context", json={"scope": "category", "context_id": cat["slug"], "version": 1, "payload": cat})
    for m in merchants.values():
        requests.post(f"{base_url}/v1/context", json={"scope": "merchant", "context_id": m["merchant_id"], "version": 1, "payload": m})
    for c in customers.values():
        requests.post(f"{base_url}/v1/context", json={"scope": "customer", "context_id": c["customer_id"], "version": 1, "payload": c})
    for t in triggers.values():
        requests.post(f"{base_url}/v1/context", json={"scope": "trigger", "context_id": t["id"], "version": 1, "payload": t})
        
    print(f"Pushed {len(categories)} categories, {len(merchants)} merchants, {len(customers)} customers, {len(triggers)} triggers.")
    
    now_str = "2026-04-26T10:35:00Z"
    
    print(f"Processing {len(pairs)} pairs...")
    for pair in pairs:
        tid = pair["trigger_id"]
        # Trigger single pair tick
        print(f"Tick for pair {pair['test_id']} (trigger: {tid})")
        resp = requests.post(f"{base_url}/v1/tick", json={
            "now": now_str,
            "available_triggers": [tid]
        })
        
        if resp.status_code == 200:
            actions = resp.json().get("actions", [])
            for action in actions:
                # Add required missing test_id for submission
                action["test_id"] = pair["test_id"]
                submission_lines.append(json.dumps(action))
                print(f"  -> Generated action for {pair['test_id']}")
                break
            if not actions:
                print(f"  -> No action returned for {pair['test_id']}")
        else:
            print(f"  -> Error {resp.status_code} for {pair['test_id']}")

    with open("submission.jsonl", "w") as f:
        f.write("\n".join(submission_lines))
        
    print(f"Generated submission.jsonl with {len(submission_lines)} entries.")

if __name__ == "__main__":
    generate_submission()
