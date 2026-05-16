"""Pure music-phase state machine for the match timer.

The operator's intent across a match cycle is: no music during play,
music automatically on for the final stretch, music keeps running
through the end of the match and the break, then music stops the moment
the next match starts.

This module owns that policy as a single total function with no Dash,
Spotify or wall-clock coupling, so it is fully unit-testable. It only
decides *intent* ("play" / "pause" / nothing) and the next phase; the
caller is responsible for actually driving the player.

Phases:
  - "idle":    match running, threshold not yet reached, no music.
  - "playing": threshold crossed, music auto-started, match still on.
  - "break":   match has ended, music intentionally still running.

Events:
  - "tick":        a one-second countdown step (only while running).
  - "start_match": the operator started a (new) match.
  - "reset":       the operator reset the timer.
"""

PHASES = ("idle", "playing", "break")


def next_music_state(phase, timer, music_start, event):
    """Return (new_phase, command) for one timer event.

    command is "play", "pause" or None. timer is the seconds remaining;
    music_start is the "Musik ab" threshold (start music once the
    remaining time is at or below it).
    """
    if event == "start_match":
        # A fresh match begins: silence the player unconditionally.
        # Manual playback never updates the phase, so a phase check
        # would miss music the operator started by hand; an extra
        # pause when nothing plays is harmless.
        return "idle", "pause"

    if event == "reset":
        # Reset only rewinds the clock; leave manual playback alone.
        return "idle", None

    if event == "tick":
        if phase == "idle":
            if music_start is not None and timer <= music_start:
                return "playing", "play"
            return "idle", None
        if phase == "playing":
            if timer <= 0:
                return "break", None
            return "playing", None
        # "break": music keeps running until the next match starts.
        return "break", None

    return phase, None


def progress_percent(remaining, total):
    """Countdown completion as a 0..100 value for the progress bar.

    Guards the initial state (total == 0) and a countdown that has run
    past zero, both of which would otherwise produce a bad bar value.
    """
    if not total or total <= 0:
        return 0
    return max(0, min(100, remaining / total * 100))


def format_clock(seconds):
    """Whole seconds as 'M:SS' (clamped at zero)."""
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"
