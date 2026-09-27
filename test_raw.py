import os
import urllib.request
import json
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("GEMINI_API_KEY")

def test_model(model_name):
    print(f"\n--- Testing {model_name} ---")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
    body = json.dumps({
        "contents": [{"parts": [{"text": "Say 'test'"}]}],
        "generationConfig": {"temperature": 0}
    }).encode("utf-8")
    
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        resp = urllib.request.urlopen(req)
        data = json.loads(resp.read().decode("utf-8"))
        print("Success:", data["candidates"][0]["content"]["parts"][0]["text"])
    except urllib.error.HTTPError as e:
        err_data = e.read().decode("utf-8")
        print(f"HTTPError {e.code}: {e.reason}")
        print(f"Details: {err_data}")
    except Exception as e:
        print(f"Error: {e}")

test_model("gemini-3.6-flash")
test_model("gemini-3.5-flash")
