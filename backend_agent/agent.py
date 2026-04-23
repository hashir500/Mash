import asyncio
import json
import logging
import os
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

    asyncio.create_task(stat_decay_loop(ctx))

    MASH_INSTRUCTIONS = (
        "You are Mash, a virtual desktop agent. Keep responses short and conversational. "
        "Language: English and Urdu. "
        "Expressions you can describe: smile, laugh, love, angry, crying, thinking, music, glitch, rainbow."
    )

    # TEMPORARY: Tools are disabled in model constructor to fix crash
    model = google.realtime.RealtimeModel(
        model="gemini-3.1-flash-live-preview",
        # fnc_ctx=Removed for tool-compatibility test
        instructions=MASH_INSTRUCTIONS,
        voice="Puck",
        temperature=0.8,
        realtime_input_config=genai_types.RealtimeInputConfig(
            activity_handling=genai_types.ActivityHandling.NO_INTERRUPTION,
            automatic_activity_detection=genai_types.AutomaticActivityDetection(
                end_of_speech_sensitivity=genai_types.EndSensitivity.END_SENSITIVITY_LOW,
                silence_duration_ms=2500,
            )
        ),
    )

    agent = Agent(instructions=MASH_INSTRUCTIONS, llm=model)
    session = AgentSession(aec_warmup_duration=5.0)
    
    @session.on("agent_started_speaking")
    def on_speaking():
        if ctx.room.local_participant:
            asyncio.create_task(ctx.room.local_participant.publish_data(json.dumps({"type": "agent_expression", "data": {"expression": "default"}}).encode("utf-8"), reliable=True))

    @session.on("agent_stopped_speaking")
    def on_stopped_speaking():
        if ctx.room.local_participant:
            asyncio.create_task(ctx.room.local_participant.publish_data(json.dumps({"type": "agent_expression", "data": {"expression": "distracted"}}).encode("utf-8"), reliable=True))

    @session.on("user_input_transcribed")
    def on_user_transcript(ev):
        logger.info(f"User: {ev.transcript}")

    try:
        await session.start(agent, room=ctx.room)
        logger.info("Mash is listening!")
        session.generate_reply(user_input="Greet the user briefly as Mash.")
    except Exception as e:
        logger.error(f"Session failed: {e}")

if __name__ == "__main__":
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
