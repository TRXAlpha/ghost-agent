"""Ghost Pet — the little spirit that watches over your coding sessions.

A tiny ASCII ghost with emotions that reacts to what the agent is doing.
Visible in the terminal TUI corner.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class PetState(Enum):
    IDLE = "idle"
    THINKING = "thinking"
    WORKING = "working"
    REPAIRING = "repairing"
    SUCCESS = "success"
    FAILURE = "failure"
    WELCOME = "welcome"
    BYE = "bye"


@dataclass
class PetEmotion:
    state: PetState
    face: str
    message: str
    color: str  # ANSI color code
    bounce: bool = False


# ── Emotion definitions ──────────────────────────────────────────────────────

EMOTIONS: dict[PetState, PetEmotion] = {
    PetState.IDLE: PetEmotion(
        state=PetState.IDLE,
        face="(-_^)",
        message="zzz",
        color="\033[90m",  # gray
    ),
    PetState.THINKING: PetEmotion(
        state=PetState.THINKING,
        face="(o_o)",
        message="hmm...",
        color="\033[36m",  # cyan
    ),
    PetState.WORKING: PetEmotion(
        state=PetState.WORKING,
        face="(^_^)/",
        message="on it!",
        color="\033[32m",  # green
        bounce=True,
    ),
    PetState.REPAIRING: PetEmotion(
        state=PetState.REPAIRING,
        face="(@_@)",
        message="fixing...",
        color="\033[33m",  # yellow
    ),
    PetState.SUCCESS: PetEmotion(
        state=PetState.SUCCESS,
        face="(*_*)",
        message="done!",
        color="\033[92m",  # bright green
    ),
    PetState.FAILURE: PetEmotion(
        state=PetState.FAILURE,
        face="(T_T)",
        message="oops...",
        color="\033[31m",  # red
    ),
    PetState.WELCOME: PetEmotion(
        state=PetState.WELCOME,
        face="(^_^)/",
        message="hi!",
        color="\033[95m",  # bright magenta
    ),
    PetState.BYE: PetEmotion(
        state=PetState.BYE,
        face="(^_^)/",
        message="bye!",
        color="\033[95m",  # bright magenta
    ),
}

RESET = "\033[0m"

# ── Ghost body frames for animation ──────────────────────────────────────────

GHOST_BODY = [
    # Frame 0: normal
    "  \\|/ ",
    " (._.) ",
    "  |||  ",
]
GHOST_BODY_BOUNCE = [
    # Frame 0: normal
    "  \\|/ ",
    " (._.) ",
    "  |||  ",
    # Frame 1: bounce up
    "  \\|/ ",
    "(._.) ",
    "  |||  ",
]


@dataclass
class GhostPet:
    """The Ghost pet that reacts to agent activity."""

    state: PetState = PetState.IDLE
    _last_state: PetState = field(default=PetState.IDLE, repr=False)
    _anim_frame: int = field(default=0, repr=False)
    _state_changed_at: float = field(default_factory=time.time, repr=False)
    _visible: bool = field(default=True, repr=False)

    def set_state(self, new_state: PetState) -> None:
        """Change the pet's emotional state."""
        if new_state != self.state:
            self._last_state = self.state
            self.state = new_state
            self._state_changed_at = time.time()
            self._anim_frame = 0

    def get_emotion(self) -> PetEmotion:
        """Get the current emotion data."""
        return EMOTIONS[self.state]

    def render_inline(self) -> str:
        """Render the pet as a single-line string for terminal display."""
        emotion = self.get_emotion()
        color = emotion.color
        # Bounce animation for WORKING state
        face = emotion.face
        if emotion.bounce:
            self._anim_frame = (self._anim_frame + 1) % 4
            if self._anim_frame in (1, 3):
                face = emotion.face.replace(")", " \\") if "\\" not in emotion.face else emotion.face
        return f"{color}{face} {emotion.message}{RESET}"

    def render_box(self, width: int = 20) -> str:
        """Render the pet as a small box for the TUI corner."""
        emotion = self.get_emotion()
        color = emotion.color
        face = emotion.face
        msg = emotion.message
        status = self.state.value.upper()

        # Ghost body
        lines = [
            f"{color}  \\|/  {RESET}",
            f"{color} (._.) {RESET}",
            f"{color}  |||  {RESET}",
        ]

        # Face and message
        face_line = f" {color}{face}{RESET}"
        msg_line = f" {color}{msg}{RESET}"
        status_line = f" \033[2m{status}{RESET}"

        box_width = width
        top = f" \033[2m{'.' * box_width}{RESET}"
        bottom = f" \033[2m{'`' * box_width}{RESET}"

        return "\n".join([
            top,
            f" {color}GHOST{RESET}",
            "",
            face_line,
            msg_line,
            "",
            status_line,
            bottom,
        ])

    def render_compact(self) -> str:
        """Render a minimal one-line representation for status bars."""
        emotion = self.get_emotion()
        return f"{emotion.color}{emotion.face}{RESET}"

    def idle_tick(self) -> str:
        """Called periodically when idle. Returns animation frame."""
        elapsed = time.time() - self._state_changed_at
        dots = "." * (int(elapsed) % 4)
        emotion = self.get_emotion()
        return f"{emotion.color}{emotion.face} {emotion.message}{dots}{RESET}"


def phase_to_pet_state(phase: str) -> PetState:
    """Map an orchestrator phase to a pet state."""
    mapping = {
        "INGEST": PetState.THINKING,
        "PLAN": PetState.THINKING,
        "IMPLEMENT": PetState.WORKING,
        "VERIFY": PetState.THINKING,
        "REPAIR": PetState.REPAIRING,
        "DONE": PetState.SUCCESS,
    }
    return mapping.get(phase, PetState.IDLE)


def result_to_pet_state(result: str) -> Optional[PetState]:
    """Map a final result to a pet state. Returns None for intermediate states."""
    if result in ("tests_passed", "no_test_cmd"):
        return PetState.SUCCESS
    elif result == "iteration_limit":
        return PetState.FAILURE
    elif "failed" in result:
        return PetState.FAILURE
    return None
