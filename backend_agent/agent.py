"""
backend_agent/agent.py
======================
Mash – Core LiveKit Agent Brain  (livekit-agents v1.5.6)

Architecture
------------
* Connects to the LiveKit cloud room as a worker (via `livekit-agents` CLI).
* Runs a full VAD → STT → LLM → TTS voice pipeline using Google Gemini / Cloud Speech.
* Maintains an internal stat-machine (energy, mood) with passive decay.
* Broadcasts all state / stat changes over the LiveKit data channel (topic: "mash-events")
  so any client (desktop UI or Tabbie hardware) can react without knowing the AI stack.

Env vars loaded from .env at project root
------------------------------------------
  LIVEKIT_URL          wss://…
  LIVEKIT_API_KEY      API…
  LIVEKIT_API_SECRET   …
  GOOGLE_API_KEY       …
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

# ── Path bootstrap so we can import shared/ from any cwd ─────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── Load .env from project root ───────────────────────────────────────────────
from dotenv import load_dotenv  # type: ignore
load_dotenv(PROJECT_ROOT / ".env")

# ── LiveKit agents ────────────────────────────────────────────────────────────
from livekit import agents, rtc, api
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    llm,
)
from livekit.agents.voice.events import AgentStateChangedEvent, ConversationItemAddedEvent
from livekit.plugins import silero
from livekit.plugins.google.beta import realtime

# ── Shared event constants ────────────────────────────────────────────────────
from shared.events import (
    DATA_TOPIC,
    EVT_GREETING, EVT_HEARTBEAT, EVT_STATE_CHANGE, EVT_STAT_UPDATE, EVT_TRANSCRIPT,
    ROOM_NAME,
    STAT_MAX, STAT_MIN, ENERGY_LOW,
    STATE_IDLE, STATE_LISTENING, STATE_THINKING, STATE_SPEAKING, STATE_SLEEPING,
)

logger = logging.getLogger("mash.agent")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

# ─────────────────────────────────────────────────────────────────────────────
# Stat Machine  – energy & mood with passive decay
# ─────────────────────────────────────────────────────────────────────────────
class StatMachine:
    DECAY_INTERVAL  = 30     # seconds between passive ticks
    ENERGY_DECAY    = 2      # points lost per idle tick
    MOOD_DECAY      = 1
    ENERGY_RESTORE  = 15     # points gained when user speaks
    MOOD_RESTORE    = 10     # points gained when agent responds

    def __init__(self) -> None:
        self.energy: float = 80.0
        self.mood:   float = 75.0
        self._state: str   = STATE_IDLE
        self._listeners: list = []

    # ── State property ────────────────────────────────────────────────────────
    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, value: str) -> None:
        if value != self._state:
            self._state = value
            self._notify()

    def add_listener(self, cb) -> None:
        self._listeners.append(cb)

    def _notify(self) -> None:
        for cb in self._listeners:
            try: cb(self)
            except Exception as e: logger.warning("stat listener: %s", e)

    # ── User interaction boosts ───────────────────────────────────────────────
    def on_user_spoke(self) -> None:
        self.energy = min(STAT_MAX, self.energy + self.ENERGY_RESTORE)
        self._notify()

    def on_agent_responded(self) -> None:
        self.mood = min(STAT_MAX, self.mood + self.MOOD_RESTORE)
        self._notify()

    # ── Passive decay loop (background task) ──────────────────────────────────
    async def decay_loop(self) -> None:
        while True:
            await asyncio.sleep(self.DECAY_INTERVAL)
            if self._state in (STATE_IDLE, STATE_SLEEPING):
                self.energy = max(STAT_MIN, self.energy - self.ENERGY_DECAY)
                self.mood   = max(STAT_MIN, self.mood   - self.MOOD_DECAY)
                # Auto-sleep / auto-wake based on energy level
                if self.energy <= ENERGY_LOW and self._state != STATE_SLEEPING:
                    self.state = STATE_SLEEPING
                elif self.energy > ENERGY_LOW and self._state == STATE_SLEEPING:
                    self.state = STATE_IDLE
                else:
                    self._notify()   # still emit stat_update even without state change

    def to_dict(self) -> dict:
        return {"energy": round(self.energy, 1), "mood": round(self.mood, 1)}


# ─────────────────────────────────────────────────────────────────────────────
# Data Channel Broadcaster
# ─────────────────────────────────────────────────────────────────────────────
class Broadcaster:
    """Wraps the LiveKit room and publishes typed JSON messages on DATA_TOPIC."""

    def __init__(self, room: rtc.Room) -> None:
        self._room = room

    def _publish(self, event_type: str, payload: dict[str, Any]) -> None:
        msg = json.dumps({"type": event_type, "payload": payload}).encode()
        asyncio.create_task(
            self._room.local_participant.publish_data(
                msg,
                topic=DATA_TOPIC,
                reliable=True,
            )
        )

    def state_change(self, state: str) -> None:
        logger.debug("broadcast → state_change: %s", state)
        self._publish(EVT_STATE_CHANGE, {"state": state})

    def stat_update(self, stats: dict) -> None:
        self._publish(EVT_STAT_UPDATE, stats)

    def transcript(self, role: str, text: str) -> None:
        self._publish(EVT_TRANSCRIPT, {"role": role, "text": text})

    def greeting(self) -> None:
        self._publish(EVT_GREETING, {"message": "Mash brain online"})

    def heartbeat(self, ts: float) -> None:
        self._publish(EVT_HEARTBEAT, {"ts": ts})


# ─────────────────────────────────────────────────────────────────────────────
# Agent State Mapping  – livekit-agents v1.5.6 uses string literals
# ─────────────────────────────────────────────────────────────────────────────
_LK_STATE_MAP: dict[str, str] = {
    "initializing": STATE_IDLE,
    "idle":         STATE_IDLE,
    "listening":    STATE_LISTENING,
    "thinking":     STATE_THINKING,
    "speaking":     STATE_SPEAKING,
}


# ─────────────────────────────────────────────────────────────────────────────
# Mash Agent
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are Mash, a witty, slightly sarcastic but warm desktop companion AI.
Your physical form is a small glowing orb that floats on the user's screen.
Keep responses concise and conversational – you live on a small widget, not a chat window.
Express personality through word choice, not length.
When the user seems idle, you can gently nudge them.
"""


class MashAgent(Agent):
    """
    The agent voice personality.  livekit-agents v1.5.6 lifecycle:
      • on_enter()               – called once when session starts
      • on_user_turn_completed() – called when user finishes speaking
      • on_exit()                – called on shutdown
    """

    def __init__(self, stats: StatMachine, broadcaster: Broadcaster) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)
        self._stats       = stats
        self._broadcaster = broadcaster

    async def on_enter(self, *args, **kwargs) -> None:
        # Give a small delay before UI fade-in completes
        await asyncio.sleep(1.2)
        # Note: We cannot use self.session.say() because Gemini Multimodal handles 
        # audio generation natively. The user will initiate the conversation.
        logger.info("Agent entered and ready for input.")

    async def on_user_turn_completed(
        self,
        turn_ctx: llm.ChatContext,
        new_message: llm.ChatMessage,
    ) -> None:
        """User finished speaking → boost energy, log transcript, let pipeline continue."""
        self._stats.on_user_spoke()
        self._broadcaster.stat_update(self._stats.to_dict())
        # Extract text for transcript broadcast
        text = ""
        if hasattr(new_message, "text_content"):
            text = new_message.text_content() or ""
        elif hasattr(new_message, "content"):
            content = new_message.content
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                text = " ".join(
                    c.text if hasattr(c, "text") else str(c)
                    for c in content
                    if hasattr(c, "text") or isinstance(c, str)
                )
        if text:
            self._broadcaster.transcript("user", text)


# ─────────────────────────────────────────────────────────────────────────────
# Standalone execution  (no LiveKit Worker Cloud Dispatch required)
# ─────────────────────────────────────────────────────────────────────────────
async def manual_main() -> None:
    room = rtc.Room()
    # Create an automatic token to join the room
    token = (
        api.AccessToken(os.environ.get("LIVEKIT_API_KEY", ""), os.environ.get("LIVEKIT_API_SECRET", ""))
        .with_identity("mash-brain")
        .with_name("Mash Brain")
        .with_grants(
            api.VideoGrants(
                room_join=True,
                room=ROOM_NAME,
            )
        )
        .to_jwt()
    )

    logger.info("Mash agent connecting directly to room: %s", ROOM_NAME)
    url = os.environ.get("LIVEKIT_URL", "")
    await room.connect(url, token)

    stats       = StatMachine()
    broadcaster = Broadcaster(room)

    # ── Wire stat-machine changes → data channel ──────────────────────────────
    def _on_stat_change(sm: StatMachine) -> None:
        broadcaster.state_change(sm.state)
        broadcaster.stat_update(sm.to_dict())

    stats.add_listener(_on_stat_change)

    # ── Build voice session (Gemini Multimodal Live) ──────────────────────
    try:
        session = AgentSession(
            # VAD: Silero (local, fast)
            vad=silero.VAD.load(),
            
            # Use native Gemini Live! No STT/TTS keys needed!
            llm=realtime.RealtimeModel(
                model="gemini-2.5-flash-native-audio-latest",
                voice="Aoede"
            ),
            
            min_endpointing_delay=0.4,
            max_endpointing_delay=6.0,
        )
    except Exception as exc:
        logger.error("Failed to start AgentSession: %s", exc)
        await asyncio.sleep(5)
        return


    agent = MashAgent(stats, broadcaster)

    # ── Hook AgentSession events → stat machine ───────────────────────────────
    @session.on("agent_state_changed")
    def _on_agent_state(ev: AgentStateChangedEvent) -> None:
        new_state_str = str(ev.new_state).lower()
        # v1.5.6 returns e.g. "AgentState.listening" or just "listening"
        for k, v in _LK_STATE_MAP.items():
            if k in new_state_str:
                if v != stats.state:
                    stats.state = v
                break

    @session.on("conversation_item_added")
    def _on_item_added(ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        role = getattr(item, "role", "agent")
        role_str = str(role).lower()
        # Extract text content from the message
        text = ""
        if hasattr(item, "text_content"):
            try:
                text = item.text_content() or ""
            except Exception:
                pass
        elif hasattr(item, "content"):
            content = item.content
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for c in content:
                    if hasattr(c, "text"):
                        parts.append(c.text)
                    elif isinstance(c, str):
                        parts.append(c)
                text = " ".join(parts)
        if text and "agent" in role_str:
            stats.on_agent_responded()
            broadcaster.stat_update(stats.to_dict())
            broadcaster.transcript("agent", text)

    # ── Start background loops ────────────────────────────────────────────────
    broadcaster.greeting()
    asyncio.create_task(stats.decay_loop())

    async def _heartbeat_loop() -> None:
        while True:
            broadcaster.heartbeat(time.time())
            await asyncio.sleep(10)

    asyncio.create_task(_heartbeat_loop())

    # ── Start the session (attaches to room audio/video) ─────────────────────
    await session.start(agent=agent, room=room)
    logger.info("Mash backend session active! Waiting forever...")
    
    # Keep the script running forever
    await asyncio.Event().wait()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(manual_main())
