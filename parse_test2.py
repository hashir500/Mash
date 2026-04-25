import asyncio
from google import genai
from google.genai import types

data = b"hello"
try:
    part = types.Part.from_bytes(data=data, mime_type="audio/pcm")
    print(part)
except Exception as e:
    print("from_bytes failed:", e)
