import asyncio
import json
import logging
from dotenv import load_dotenv

from google.genai import types as genai_types

from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli, Agent, AgentSession, llm
from livekit.plugins import google
from livekit import rtc

load_dotenv()
logging.basicConfig(level=logging.INFO)
logging.getLogger("livekit").setLevel(logging.DEBUG)
logger = logging.getLogger("mash-agent")

async def stat_decay_loop(ctx: JobContext):
    """Background task to simulate stat decay (e.g. energy) and broadcast it."""
    energy = 100
    last_expression = "distracted"
    while True:
        await asyncio.sleep(2)
        energy = max(0, energy - 1)
        if energy == 0:
            energy = 100
        
        # Determine auto-expression based on energy
        expression = "distracted" if energy > 20 else "sleepy"
        
        state_payload = {
            "type": "agent_state",
            "data": {
                "energy": energy,
                "status": "idle" if energy > 20 else "tired"
            }
        }
        
        # Publish state
        if ctx.room and ctx.room.local_participant:
            payload_bytes = json.dumps(state_payload).encode("utf-8")
            await ctx.room.local_participant.publish_data(payload_bytes, reliable=True)
            
            # Publish expression change if it changed
            if expression != last_expression:
                expr_payload = {"type": "agent_expression", "data": {"expression": expression}}
                await ctx.room.local_participant.publish_data(json.dumps(expr_payload).encode("utf-8"), reliable=True)
                last_expression = expression
            
            logger.debug(f"Published state: {state_payload}")

class MashActions(llm.FunctionContext):
    def __init__(self, participant: rtc.LocalParticipant):
        super().__init__()
        self.participant = participant

    @llm.ai_callable(description="Set the visual expression of the agent.")
    async def set_expression(self, 
        expression: str = llm.Annotated[str, llm.TypeInfo(description="The name of the video file to play (e.g. smile, love, angry, laugh)")]
    ):
        logger.info(f"AI requested expression: {expression}")
        expr_payload = {"type": "agent_expression", "data": {"expression": expression}}
        await self.participant.publish_data(json.dumps(expr_payload).encode("utf-8"), reliable=True)

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
        "- Keep responses concise and conversational.\n"
        "- Available visual expressions you can use: smile, laugh, love, angry, crying, thinking, music, glitch, rainbow."
    )

    fnc_ctx = MashActions(ctx.room.local_participant)

    # Initialize the Multimodal Agent using Gemini Live API
    model = google.realtime.RealtimeModel(
        model="gemini-3.1-flash-live-preview",
        fnc_ctx=fnc_ctx,
        instructions=MASH_INSTRUCTIONS,
        voice="Puck",
        temperature=0.8,
        # No 'language' param → Gemini auto-detects the spoken language.
        realtime_input_config=genai_types.RealtimeInputConfig(
            activity_handling=genai_types.ActivityHandling.NO_INTERRUPTION,
            automatic_activity_detection=genai_types.AutomaticActivityDetection(
                end_of_speech_sensitivity=genai_types.EndSensitivity.END_SENSITIVITY_LOW,
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

    @session.on("agent_started_speaking")
    def on_speaking():
        # Switch to default talking video
        expr_payload = {"type": "agent_expression", "data": {"expression": "default"}}
        asyncio.create_task(ctx.room.local_participant.publish_data(json.dumps(expr_payload).encode("utf-8"), reliable=True))

    @session.on("agent_stopped_speaking")
    def on_stopped_speaking():
        # Switch back to distracted (idle) video
        expr_payload = {"type": "agent_expression", "data": {"expression": "distracted"}}
        asyncio.create_task(ctx.room.local_participant.publish_data(json.dumps(expr_payload).encode("utf-8"), reliable=True))

    @session.on("error")
    def on_error(ev):
        logger.error(f"Session Error: {ev.error}")

    # Start the session and handle errors
    try:
        logger.info(f"Starting AgentSession with model: {model.model}...")
        await session.start(agent, room=ctx.room)
        logger.info("Agent session started and connected to Gemini! Mash is listening and ready!")
        
        # Give the session a moment to be internally "running" before the first reply
        await asyncio.sleep(0.5) # Reduced delay for faster greeting
        logger.info("Triggering initial greeting...")
        session.generate_reply(user_input="Introduce yourself as Mash, the desktop digital twin. Keep it very short and greet the user.")

    except Exception as e:
        logger.error(f"Failed to start agent session: {e}")
        # If it's a 1008 error, it might be caught here or in the on("error") handler

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))

