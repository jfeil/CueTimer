"""Unit tests for the pure music-phase state machine."""

from timer_logic import next_music_state, progress_percent, format_clock


# --- start_match ---------------------------------------------------------

def test_start_match_always_silences_even_from_idle():
    # Manual playback never sets the phase, so a match start must pause
    # regardless of the phase the machine believes it is in.
    assert next_music_state("idle", 200, 10, "start_match") == ("idle", "pause")


def test_start_match_during_playing_pauses():
    assert next_music_state("playing", 5, 10, "start_match") == ("idle", "pause")


def test_start_match_during_break_pauses_carryover_music():
    assert next_music_state("break", -3, 10, "start_match") == ("idle", "pause")


# --- reset ---------------------------------------------------------------

def test_reset_returns_idle_without_touching_playback():
    assert next_music_state("break", 0, 10, "reset") == ("idle", None)


# --- tick: idle ----------------------------------------------------------

def test_tick_idle_above_threshold_stays_idle():
    assert next_music_state("idle", 50, 10, "tick") == ("idle", None)


def test_tick_idle_at_threshold_starts_music():
    assert next_music_state("idle", 10, 10, "tick") == ("playing", "play")


def test_tick_idle_below_threshold_starts_music():
    assert next_music_state("idle", 9, 10, "tick") == ("playing", "play")


def test_tick_idle_without_threshold_never_starts():
    assert next_music_state("idle", 0, None, "tick") == ("idle", None)


# --- tick: playing / break ----------------------------------------------

def test_tick_playing_keeps_playing_until_zero():
    assert next_music_state("playing", 3, 10, "tick") == ("playing", None)


def test_tick_playing_at_zero_enters_break_without_stopping():
    assert next_music_state("playing", 0, 10, "tick") == ("break", None)


def test_tick_break_keeps_music_running():
    assert next_music_state("break", -10, 10, "tick") == ("break", None)


# --- unknown event -------------------------------------------------------

def test_unknown_event_is_inert():
    assert next_music_state("playing", 5, 10, "noop") == ("playing", None)


# --- progress_percent ----------------------------------------------------

def test_progress_percent_normal_range():
    assert progress_percent(50, 200) == 25
    assert progress_percent(200, 200) == 100


def test_progress_percent_zero_total_does_not_divide():
    assert progress_percent(0, 0) == 0
    assert progress_percent(10, 0) == 0


def test_progress_percent_clamps_out_of_range():
    assert progress_percent(-5, 200) == 0      # ran past zero
    assert progress_percent(300, 200) == 100   # never exceeds full


# --- format_clock --------------------------------------------------------

def test_format_clock_minutes_and_seconds():
    assert format_clock(200) == "3:20"
    assert format_clock(9) == "0:09"
    assert format_clock(0) == "0:00"


def test_format_clock_clamps_negative_and_none():
    assert format_clock(-5) == "0:00"
    assert format_clock(None) == "0:00"
