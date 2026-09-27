import os
from google import genai
from dotenv import load_dotenv

load_dotenv()

client = genai.Client()

def test_model(model_name):
    print(f"\n--- Testing {model_name} ---")
    try:
        res = client.interactions.create(
            model=model_name,
            input="Say 'test'",
            generation_config={"temperature": 0}
        )
        print("Success:", res.output_text.strip())
    except Exception as e:
        print(f"Error: {e}")

test_model("gemini-3.6-flash")
test_model("gemini-3.5-flash")
