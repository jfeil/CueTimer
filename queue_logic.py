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
        "start_ms": 0,
    }


def clamp_start_ms(start_ms, duration_ms):
    """Keep a per-song start offset inside the track.

    Never negative and never at/after the end (which would start a
    track that is already over). Falls back to non-negative when the
    duration is unknown.
    """
    start = max(0, int(start_ms or 0))
    if not duration_ms or duration_ms <= 0:
        return start
    return min(start, max(duration_ms - 1000, 0))


def set_start_ms(queue, row_id, start_ms):
    """Return the queue with one entry's clamped start offset updated."""
    updated = []
    for entry in queue or []:
        if entry["rowId"] == row_id:
            entry = dict(entry)
            entry["start_ms"] = clamp_start_ms(start_ms,
                                               entry.get("duration_ms", 0))
        updated.append(entry)
    return updated


def with_new_row_id(entry):
    """Return a copy of a queue entry with a fresh rowId.

    Adding the same searched/playlist track more than once must yield
    independent rows, otherwise removal and reordering (which key on
    rowId) would act on every copy at once.
    """
    clone = dict(entry)
    clone["rowId"] = uuid.uuid4().hex
    return clone


def reorder_queue(queue, ordered_row_ids):
    """Return the queue rearranged to match a list of rowIds.

    Entries named in ordered_row_ids come first, in that order; any
    entry not named (e.g. one that finished or was added during a drag)
    keeps its relative position at the end. Unknown ids are ignored, so
    a stale drag payload can never drop tracks.
    """
    by_id = {entry["rowId"]: entry for entry in queue or []}
    named = [by_id[r] for r in (ordered_row_ids or []) if r in by_id]
    named_ids = {entry["rowId"] for entry in named}
    rest = [entry for entry in queue or []
            if entry["rowId"] not in named_ids]
    return named + rest


def played_split(queue, row_id):
    """Split the queue into (played, upcoming) around the current song.

    Everything up to and including row_id counts as played/current and
    is hidden; the rest is what's still to come, with the next title
    first. When nothing is selected yet (row_id is None) or it is no
    longer in the queue, nothing is played and the whole queue is
    upcoming.
    """
    queue = list(queue or [])
    if row_id is None:
        return [], queue
    for idx, entry in enumerate(queue):
        if entry["rowId"] == row_id:
            return queue[:idx + 1], queue[idx + 1:]
    return [], queue


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
