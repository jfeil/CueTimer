"""Unit tests for the pure queue model.

These exercise queue_logic in isolation: no Dash, no Spotify, no env.
"""

from queue_logic import (track_to_item, find_entry, step_queue,
                          with_new_row_id, reorder_queue,
                          clamp_start_ms, set_start_ms, played_split)


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


def test_track_to_item_defaults_start_ms_to_zero():
    assert track_to_item({"uri": "u", "name": "n", "artists": []})["start_ms"] == 0


# --- clamp_start_ms ------------------------------------------------------

def test_clamp_start_ms_within_range_kept():
    assert clamp_start_ms(15000, 200000) == 15000


def test_clamp_start_ms_negative_becomes_zero():
    assert clamp_start_ms(-5000, 200000) == 0


def test_clamp_start_ms_capped_before_track_end():
    assert clamp_start_ms(999999, 200000) == 199000  # duration - 1s


def test_clamp_start_ms_unknown_duration_only_floors():
    assert clamp_start_ms(8000, 0) == 8000
    assert clamp_start_ms(-1, 0) == 0


# --- set_start_ms --------------------------------------------------------

def test_set_start_ms_updates_only_target_and_clamps():
    queue = [
        {"rowId": "a", "duration_ms": 100000, "start_ms": 0},
        {"rowId": "b", "duration_ms": 100000, "start_ms": 0},
    ]
    result = set_start_ms(queue, "b", 999999)
    assert result[0]["start_ms"] == 0
    assert result[1]["start_ms"] == 99000          # clamped to dur - 1s


def test_set_start_ms_does_not_mutate_source():
    queue = [{"rowId": "a", "duration_ms": 100000, "start_ms": 0}]
    set_start_ms(queue, "a", 5000)
    assert queue[0]["start_ms"] == 0


# --- with_new_row_id -----------------------------------------------------

def test_with_new_row_id_changes_only_the_id():
    original = make_queue("a")[0]
    clone = with_new_row_id(original)
    assert clone["rowId"] != original["rowId"]
    assert {k: v for k, v in clone.items() if k != "rowId"} == \
           {k: v for k, v in original.items() if k != "rowId"}


def test_with_new_row_id_does_not_mutate_source():
    original = make_queue("a")[0]
    with_new_row_id(original)
    assert original["rowId"] == "a"


# --- find_entry ----------------------------------------------------------

def test_find_entry_returns_match():
    queue = make_queue("a", "b", "c")
    assert find_entry(queue, "b")["name"] == "b"


def test_find_entry_missing_returns_none():
    assert find_entry(make_queue("a"), "zzz") is None


def test_find_entry_empty_and_none_queue():
    assert find_entry([], "a") is None
    assert find_entry(None, "a") is None


# --- reorder_queue -------------------------------------------------------

def test_reorder_applies_new_order():
    queue = make_queue("a", "b", "c")
    result = reorder_queue(queue, ["c", "a", "b"])
    assert [e["rowId"] for e in result] == ["c", "a", "b"]


def test_reorder_appends_unnamed_entries_in_place():
    queue = make_queue("a", "b", "c", "d")
    result = reorder_queue(queue, ["c", "a"])
    assert [e["rowId"] for e in result] == ["c", "a", "b", "d"]


def test_reorder_ignores_unknown_ids_without_dropping_tracks():
    queue = make_queue("a", "b")
    result = reorder_queue(queue, ["ghost", "b", "a"])
    assert [e["rowId"] for e in result] == ["b", "a"]


def test_reorder_empty_order_keeps_queue():
    queue = make_queue("a", "b")
    assert reorder_queue(queue, []) == queue
    assert reorder_queue(queue, None) == queue


# --- played_split --------------------------------------------------------

def test_played_split_hides_up_to_and_including_current():
    queue = make_queue("a", "b", "c", "d")
    played, upcoming = played_split(queue, "b")
    assert [e["rowId"] for e in played] == ["a", "b"]
    assert [e["rowId"] for e in upcoming] == ["c", "d"]


def test_played_split_none_means_all_upcoming():
    queue = make_queue("a", "b")
    played, upcoming = played_split(queue, None)
    assert played == []
    assert [e["rowId"] for e in upcoming] == ["a", "b"]


def test_played_split_unknown_row_means_all_upcoming():
    queue = make_queue("a", "b")
    assert played_split(queue, "ghost") == ([], queue)


def test_played_split_empty_queue():
    assert played_split([], "a") == ([], [])


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
