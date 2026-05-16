"""Pure Spotify-shape helpers for the playlist import path.

No network, Dash or env coupling: parsing a playlist reference and
selecting the playable tracks out of a playlist-items page are both
total functions of their input, so they are unit-tested directly.
"""

import re

_PLAYLIST_REF = re.compile(r"playlist[:/]([A-Za-z0-9]+)")
_BARE_ID = re.compile(r"^[A-Za-z0-9]{22}$")


def parse_playlist_id(text):
    """Extract a playlist id from a URI, URL or bare id.

    Accepts e.g. ``spotify:playlist:<id>``,
    ``https://open.spotify.com/playlist/<id>?si=…`` or a raw 22-char
    id. Returns None when nothing playlist-like is found.
    """
    if not text:
        return None
    text = text.strip()
    match = _PLAYLIST_REF.search(text)
    if match:
        return match.group(1)
    if _BARE_ID.match(text):
        return text
    return None


def extract_playlist_tracks(items):
    """Return the playable track objects from a playlist-items page.

    Skips entries Spotify cannot stream through the Web API: removed
    tracks (``track`` is null), local files and anything without a uri.
    """
    tracks = []
    for entry in items or []:
        track = entry.get("track")
        if not track or track.get("is_local") or not track.get("uri"):
            continue
        tracks.append(track)
    return tracks


def playlist_error_message(exc, playlist_id):
    """Turn a Spotify API error into something the operator can act on.

    Spotify-owned algorithmic/editorial playlists (the 37i9dQZF1…
    namespace: Daily Mix, Radio, Editorial) lost third-party Web API
    access in Nov 2024 and now 404, which is otherwise baffling. Kept
    here (pure: only reads exc.http_status) so it stays unit-testable
    without importing the Spotify client.
    """
    if getattr(exc, "http_status", None) == 404:
        if (playlist_id or "").startswith("37i9dQZF1"):
            return ("Spotify-eigene bzw. algorithmische Playlists "
                    "(Daily Mix, Radio, Editorial) sind über die API "
                    "nicht zugänglich. Bitte eine eigene Playlist "
                    "verwenden (Songs ggf. in eigene Playlist kopieren).")
        return ("Playlist nicht gefunden oder nicht zugänglich — "
                "evtl. privat, gelöscht oder Spotify-eigen.")
    return f"Playlist konnte nicht geladen werden: {exc}"
