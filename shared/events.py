"""
shared/events.py
----------------
Canonical event and state definitions shared between the backend agent
and any frontend client (Mash desktop UI or Tabbie hardware).

All LiveKit data-channel messages are JSON-encoded with this schema:
  { "type": <EVENT_TYPE>, "payload": { ... } }
"""

# ──────────────────────────────────────────────
# Agent state identifiers
# ──────────────────────────────────────────────
STATE_IDLE      = "idle"        # Nothing happening; avatar rests
STATE_LISTENING = "listening"   # STT is active, picking up user audio
STATE_THINKING  = "thinking"    # LLM is processing
STATE_SPEAKING  = "speaking"    # TTS is playing back
STATE_SLEEPING  = "sleeping"    # Energy stat critically low

ALL_STATES = {STATE_IDLE, STATE_LISTENING, STATE_THINKING, STATE_SPEAKING, STATE_SLEEPING}

# ──────────────────────────────────────────────
# Data-channel event types
# ──────────────────────────────────────────────
EVT_STATE_CHANGE  = "state_change"   # { "state": STATE_* }
EVT_STAT_UPDATE   = "stat_update"    # { "energy": 0-100, "mood": 0-100 }
EVT_TRANSCRIPT    = "transcript"     # { "role": "user"|"agent", "text": "..." }
EVT_GREETING      = "greeting"       # Sent once when agent joins room
EVT_HEARTBEAT     = "heartbeat"      # Periodic keepalive

# ──────────────────────────────────────────────
# Stat bounds
# ──────────────────────────────────────────────
STAT_MAX    = 100
STAT_MIN    = 0
ENERGY_LOW  = 20   # Below this → STATE_SLEEPING

# ──────────────────────────────────────────────
# LiveKit room / topic
# ──────────────────────────────────────────────
ROOM_NAME   = "mash-room"
DATA_TOPIC  = "mash-events"
