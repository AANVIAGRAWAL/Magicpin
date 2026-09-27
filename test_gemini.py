import os
from google import genai
try:
    client = genai.Client()
    for i in range(2):
        print(f"--- Call {i+1} ---")
        res = client.interactions.create(
            model="gemini-3.5-flash",
            input="Write a 3 sentence story about a deterministic robot.",
            generation_config={"temperature": 0}
        )
        print(res.output_text)
except Exception as e:
    print(f"Error: {e}")
