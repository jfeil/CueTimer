"""Unit tests for the pure Spotify playlist helpers."""

from spotify_ids import parse_playlist_id, extract_playlist_tracks

VALID_ID = "37i9dQZF1DXcBWIGoYBM5M"


# --- parse_playlist_id ---------------------------------------------------

def test_parse_uri():
    assert parse_playlist_id(f"spotify:playlist:{VALID_ID}") == VALID_ID


def test_parse_open_url_with_query():
    url = f"https://open.spotify.com/playlist/{VALID_ID}?si=abc123"
    assert parse_playlist_id(url) == VALID_ID


def test_parse_bare_id():
    assert parse_playlist_id(VALID_ID) == VALID_ID


def test_parse_strips_whitespace():
    assert parse_playlist_id(f"  spotify:playlist:{VALID_ID}  ") == VALID_ID


def test_parse_rejects_non_playlist_and_empty():
    assert parse_playlist_id("https://open.spotify.com/track/abc") is None
    assert parse_playlist_id("not a link") is None
    assert parse_playlist_id("") is None
    assert parse_playlist_id(None) is None


# --- extract_playlist_tracks ---------------------------------------------

def test_extract_keeps_only_playable_tracks():
    items = [
        {"track": {"uri": "spotify:track:1", "name": "ok"}},
        {"track": None},                                  # removed
        {"track": {"uri": "spotify:local:x", "is_local": True}},  # local
        {"track": {"name": "no uri"}},                    # no uri
        {"track": {"uri": "spotify:track:2", "name": "ok2"}},
    ]
    kept = extract_playlist_tracks(items)
    assert [t["uri"] for t in kept] == ["spotify:track:1", "spotify:track:2"]


def test_extract_handles_empty_and_none():
    assert extract_playlist_tracks([]) == []
    assert extract_playlist_tracks(None) == []
