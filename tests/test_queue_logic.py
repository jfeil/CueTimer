"""Unit tests for the pure queue model.

These exercise queue_logic in isolation: no Dash, no Spotify, no env.
"""

from queue_logic import track_to_item, find_entry, step_queue


def make_queue(*names):
    """Build a queue of distinct entries with deterministic rowIds."""
    return [
        {"rowId": name, "uri": f"spotify:track:{name}", "name": name,
         "artist": "x", "img": None, "duration_ms": 1000}
        for name in names
    ]


# --- track_to_item -------------------------------------------------------

def test_track_to_item_extracts_core_fields():
    track = {
        "uri": "spotify:track:abc",
        "name": "Song",
        "artists": [{"name": "A"}, {"name": "B"}],
        "album": {"images": [{"url": "big"}, {"url": "small"}]},
        "duration_ms": 4200,
    }
    item = track_to_item(track)
    assert item["uri"] == "spotify:track:abc"
    assert item["name"] == "Song"
    assert item["artist"] == "A, B"
    assert item["img"] == "small"  # smallest image is last
    assert item["duration_ms"] == 4200
    assert item["rowId"]  # non-empty unique id


def test_track_to_item_handles_missing_album_and_duration():
    item = track_to_item({"uri": "u", "name": "n", "artists": []})
    assert item["img"] is None
    assert item["duration_ms"] == 0
    assert item["artist"] == ""


def test_track_to_item_row_ids_are_unique():
    track = {"uri": "u", "name": "n", "artists": []}
    assert track_to_item(track)["rowId"] != track_to_item(track)["rowId"]


# --- find_entry ----------------------------------------------------------

def test_find_entry_returns_match():
    queue = make_queue("a", "b", "c")
    assert find_entry(queue, "b")["name"] == "b"


def test_find_entry_missing_returns_none():
    assert find_entry(make_queue("a"), "zzz") is None


def test_find_entry_empty_and_none_queue():
    assert find_entry([], "a") is None
    assert find_entry(None, "a") is None


# --- step_queue ----------------------------------------------------------

def test_step_first_returns_head():
    assert step_queue(make_queue("a", "b"), None, "first")["name"] == "a"


def test_step_first_on_empty_queue_is_none():
    assert step_queue([], None, "first") is None


def test_step_next_advances():
    assert step_queue(make_queue("a", "b", "c"), "a", "next")["name"] == "b"


def test_step_next_past_end_is_none():
    assert step_queue(make_queue("a", "b"), "b", "next") is None


def test_step_prev_goes_back():
    assert step_queue(make_queue("a", "b", "c"), "c", "prev")["name"] == "b"


def test_step_prev_before_start_is_none():
    assert step_queue(make_queue("a", "b"), "a", "prev") is None


def test_step_unknown_row_next_yields_first():
    assert step_queue(make_queue("a", "b"), "ghost", "next")["name"] == "a"


def test_step_unknown_row_prev_is_none():
    assert step_queue(make_queue("a", "b"), "ghost", "prev") is None
