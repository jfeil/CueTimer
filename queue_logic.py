"""Pure queue model for the FussballTimer player.

No Dash, Spotify or environment coupling: every function here is a
total function of its arguments, so it can be reasoned about and
unit-tested in isolation.
"""

import uuid


def track_to_item(track):
    """Normalize a Spotify track object into a queue entry.

    rowId is a stable per-entry id so the same track can appear multiple
    times and drag-reorder can track rows independently of track
    identity.
    """
    images = (track.get("album") or {}).get("images") or []
    return {
        "rowId": uuid.uuid4().hex,
        "uri": track["uri"],
        "name": track["name"],
        "artist": ", ".join(a["name"] for a in track.get("artists", [])),
        "img": images[-1]["url"] if images else None,
        "duration_ms": track.get("duration_ms", 0),
    }


def with_new_row_id(entry):
    """Return a copy of a queue entry with a fresh rowId.

    Adding the same searched/playlist track more than once must yield
    independent rows, otherwise removal and reordering (which key on
    rowId) would act on every copy at once.
    """
    clone = dict(entry)
    clone["rowId"] = uuid.uuid4().hex
    return clone


def find_entry(queue, row_id):
    """Return the queue entry with the given rowId, or None."""
    for entry in queue or []:
        if entry["rowId"] == row_id:
            return entry
    return None


def step_queue(queue, current_row_id, direction):
    """Return the queue entry to play for a navigation move.

    direction is "first", "next" or "prev". Returns None when the move
    falls off either end of the queue or the queue is empty. An unknown
    current row is treated as "before the start", so "next" yields the
    first entry and "prev" yields nothing.
    """
    if not queue:
        return None
    if direction == "first":
        return queue[0]

    index = None
    for position, entry in enumerate(queue):
        if entry["rowId"] == current_row_id:
            index = position
            break

    if index is None:
        return queue[0] if direction == "next" else None

    target = index + 1 if direction == "next" else index - 1
    if 0 <= target < len(queue):
        return queue[target]
    return None
