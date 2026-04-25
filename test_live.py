import asyncio
from google import genai
from google.genai import types

client = genai.Client()

async def t():
    async with client.aio.live.connect(model='gemini-2.0-flash') as s:
        try:
            await s.send(input=types.LiveClientContent(
                realtime_input=types.LiveClientRealtimeInput(
                    media_chunks=[
                        types.Blob(
                            data=b'',
                            mime_type="audio/pcm;rate=24000"
                        )
                    ]
                )
            ))
            print("Blob syntax works")
        except Exception as e:
            print("Blob syntax failed:", e)
        try:
            await s.send(input=types.Part.from_bytes(data=b'', mime_type="audio/pcm;rate=24000"))
            print("Part syntax works")
        except Exception as e:
            print("Part syntax failed:", e)

asyncio.run(t())
