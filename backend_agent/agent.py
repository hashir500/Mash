import asyncio
import json
import logging
import os
import time
from dotenv import load_dotenv

from google.genai import types as genai_types

from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli, Agent, AgentSession, llm
from livekit.plugins import google
from livekit import rtc

# Setup logging
load_dotenv()
logging.basicConfig(level=logging.INFO)
logging.getLogger("livekit").setLevel(logging.INFO)
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
        
        expression = "distracted" if energy > 20 else "sleepy"
        state_payload = {"type": "agent_state", "data": {"energy": energy, "status": "idle" if energy > 20 else "tired"}}
        
        if ctx.room and ctx.room.local_participant:
            payload_bytes = json.dumps(state_payload).encode("utf-8")
            await ctx.room.local_participant.publish_data(payload_bytes, reliable=True)
            
            if expression != last_expression:
                expr_payload = {"type": "agent_expression", "data": {"expression": expression}}
                await ctx.room.local_participant.publish_data(json.dumps(expr_payload).encode("utf-8"), reliable=True)
                last_expression = expression

class MashActions(llm.ToolContext):
    def __init__(self, participant: rtc.LocalParticipant):
        # Tools initialization if supported
        super().__init__([]) 
        self.participant = participant

    @llm.function_tool(description="Set the visual expression of the agent.")
    async def set_expression(self, expression: str):
        logger.info(f"AI requested expression: {expression}")
        expr_payload = {"type": "agent_expression", "data": {"expression": expression}}
        await self.participant.publish_data(json.dumps(expr_payload).encode("utf-8"), reliable=True)

async def entrypoint(ctx: JobContext):
    logger.info(f"CONNECTING to room: {ctx.room.name}")
    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    
    # Wait for the human to show up
    participant = await ctx.wait_for_participant()
    logger.info(f"HUMAN {participant.identity} joined. Waking up Mash...")

    # asyncio.create_task(stat_decay_loop(ctx)) # Suspended to prevent feedback

    MASH_INSTRUCTIONS = (
        "You are Mash, a virtual desktop agent. Keep responses short and conversational. "
        "CRITICAL RULE: You MUST ONLY respond if the user explicitly says your name ('Mash'). "
        "If you do not hear the word 'Mash' in the user's speech, you MUST stay absolutely silent and ignore it. "
        "Support English and Urdu perfectly. Be friendly and conversational."
    )

    # TEMPORARY: Tools are disabled in model constructor to fix crash
    model = google.realtime.RealtimeModel(
        model="gemini-3.1-flash-live-preview",
        # fnc_ctx=Removed for tool-compatibility test
        instructions=MASH_INSTRUCTIONS,
        voice="Puck",
        temperature=0.7,
        realtime_input_config=genai_types.RealtimeInputConfig(
            activity_handling=genai_types.ActivityHandling.NO_INTERRUPTION,
            automatic_activity_detection=genai_types.AutomaticActivityDetection(
                end_of_speech_sensitivity=genai_types.EndSensitivity.END_SENSITIVITY_HIGH,
                silence_duration_ms=500,
            )
        ),
    )

    agent = Agent(instructions=MASH_INSTRUCTIONS, llm=model)
    session = AgentSession(aec_warmup_duration=0.0)
    
    _last_mute_time = 0
    _unmute_timer = None
    
    async def _do_unmute():
        await asyncio.sleep(0.5) # Wait a bit after audio stops
        logger.info("DEBUG: AUDIO STOPPED -> UNMUTING")
        if ctx.room.local_participant:
            await ctx.room.local_participant.publish_data(json.dumps({"type": "unmute_mic"}).encode("utf-8"), reliable=True)

    _last_mute_time = 0
    _unmute_timer = None
    
    @session.on("user_input_transcribed")
    def on_user_transcript(ev):
        logger.info(f"User: {ev.transcript}")

    @session.on("agent_started_speaking")
    def on_agent_speaking():
        logger.info("DEBUG: AGENT_STARTED_SPEAKING EVENT")

    @session.on("agent_stopped_speaking")
    def on_agent_stopped():
        logger.info("DEBUG: AGENT_STOPPED_SPEAKING EVENT")

    @session.on("agent_speech_interrupted")
    def on_agent_interrupted():
        logger.info("DEBUG: AGENT_SPEECH_INTERRUPTED EVENT")

    try:
        await session.start(agent, room=ctx.room)
        logger.info("Mash is listening!")
    except Exception as e:
        logger.error(f"Session failed: {e}")

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
