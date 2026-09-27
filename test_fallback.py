import sys
from unittest.mock import MagicMock
from google.genai.errors import APIError

import composer

# Mock the client to simulate a quota exceeded (429) error
def mock_create(*args, **kwargs):
    # Simulate a 429 Quota Exceeded error
    raise APIError(429, {"error": {"message": "Quota exceeded for quota metric 'Generate requests'", "code": 429}})

if composer.client is None:
    composer.client = MagicMock()

composer.client.interactions.create = mock_create

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

print("--- Simulating Quota Error (429) ---")
result = composer.compose_message("m_001", "t_recall_001", test_contexts, [], {})

import pprint
print("\n--- Fallback Output ---")
pprint.pprint(result)

# Assertions
assert result is not None, "Output should not be None"
assert "body" in result and result["body"], "Output should have a non-empty body"
assert "cta" in result and result["cta"], "Output should have a CTA"
assert result["suppression_key"] == "recall_m001_c001", "Suppression key should match"
print("\nSUCCESS: Fallback ran without crashing, produced valid output, and grounded facts correctly.")
