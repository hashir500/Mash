import asyncio
from google import genai
from google.genai import types

data = b"hello"
# We want to see how to build a LiveClientContent object with media bytes
try:
    content = types.LiveClientContent(
        realtime_input=types.LiveClientRealtimeInput(
            media_chunks=[
                types.Blob(
                    data=data,
                    mime_type="audio/pcm;rate=24000"
                )
            ]
        )
    )
    print("LiveClientContent Blob OK")
except Exception as e:
    print("Error:", e)
