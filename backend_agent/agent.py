import asyncio
import json
import logging
from dotenv import load_dotenv

from google.genai import types as genai_types

from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli, Agent, AgentSession
from livekit.plugins import google
from livekit import rtc

load_dotenv()
logging.basicConfig(level=logging.INFO)
logging.getLogger("livekit").setLevel(logging.DEBUG)
logger = logging.getLogger("mash-agent")

async def stat_decay_loop(ctx: JobContext):
    """Background task to simulate stat decay (e.g. energy) and broadcast it."""
    energy = 100
    while True:
        await asyncio.sleep(2)  # Faster tick for testing/demonstration
        energy = max(0, energy - 1)
        if energy == 0:
            energy = 100 # Reset for testing loop
        
        state_payload = {
            "type": "agent_state",
            "data": {
                "energy": energy,
                "status": "idle" if energy > 20 else "tired"
            }
        }
        
        # Publish to the data channel
        if ctx.room and ctx.room.local_participant:
            # Send as JSON string via data channel
            payload_bytes = json.dumps(state_payload).encode("utf-8")
            # In python livekit, publish_data requires reliable boolean at least as kwarg in newer versions.
            await ctx.room.local_participant.publish_data(payload_bytes, reliable=True)
            logger.debug(f"Published state: {state_payload}")

async def entrypoint(ctx: JobContext):
    logger.info(f"Connecting to room {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)

    # Launch state loop
    asyncio.create_task(stat_decay_loop(ctx))

    participant = await ctx.wait_for_participant()
    logger.info(f"Participant {participant.identity} joined, starting agent.")

    # Multilingual system instructions: Mash auto-detects English, Urdu, or mixed
    MASH_INSTRUCTIONS = (
        "You are Mash, a virtual desktop agent and digital twin. "
        "You are fully multilingual. "
        "IMPORTANT language rules:\n"
        "- If the user speaks in English, you reply in English.\n"
        "- If the user speaks in Urdu (اردو), you reply in Urdu.\n"
        "- If the user mixes English and Urdu (code-switches), you also mix naturally.\n"
        "- Never switch the language unless the user does first.\n"
        "- Keep responses concise and conversational."
    )

    # Initialize the Multimodal Agent using Gemini Live API
    model = google.realtime.RealtimeModel(
        instructions=MASH_INSTRUCTIONS,
        voice="Puck",
        temperature=0.8,
        # No 'language' param → Gemini auto-detects the spoken language.
        # Gemini 2.x understands and speaks Urdu natively even without a locale lock.
        realtime_input_config=genai_types.RealtimeInputConfig(
            # NO_INTERRUPTION: Gemini won't stop its own audio generation when it detects
            # voice activity (i.e. speaker echo picked up by the mic). It completes the
            # full sentence, then listens. Natural turn-taking is preserved.
            activity_handling=genai_types.ActivityHandling.NO_INTERRUPTION,
            automatic_activity_detection=genai_types.AutomaticActivityDetection(
                # START sensitivity: default (HIGH) so Gemini always hears the user.
                # END sensitivity: LOW so Gemini doesn't end the user's turn on brief echo.
                end_of_speech_sensitivity=genai_types.EndSensitivity.END_SENSITIVITY_LOW,
                # Wait 2.5s of continuous silence before the user turn ends.
                silence_duration_ms=2500,
                prefix_padding_ms=200,
            )
        ),

    )

    agent = Agent(
        instructions=MASH_INSTRUCTIONS,
        llm=model,
    )
    
    # aec_warmup_duration=5.0: suppress interruptions for 5s after agent starts speaking.
    # This prevents the mic from picking up speaker audio (echo) and cutting the response.
    session = AgentSession(aec_warmup_duration=5.0)
    
    @session.on("agent_transcript_received")
    def on_transcript(transcript: rtc.Transcription):
        text = " ".join([s.text for s in transcript.segments])
        logger.info(f"Agent Speaking: {text}")

    @session.on("user_input_transcribed")
    def on_user_transcript(ev):
        logger.info(f"User Said: {ev.transcript}")

    @session.on("error")
    def on_error(ev):
        logger.error(f"Session Error: {ev.error}")

    # start() with room will automatically handle track subscription via RoomIO
    await session.start(agent, room=ctx.room)
    
    # Wait for the connection to be fully established and warm up
    await asyncio.sleep(2)
    logger.info("Agent session active. Mash is listening...")
    
    # Initial greeting
    session.generate_reply(user_input="Say hello and briefly introduce yourself as Mash.")

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))

