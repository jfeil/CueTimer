"""Spotify Web API layer: OAuth, an authed client, playback control
and playlist fetching. No Dash here — callers express intent and this
hides auth, device targeting and API error handling.
"""

import os

import spotipy
from spotipy import SpotifyOAuth
from dotenv import load_dotenv

from spotify_ids import extract_playlist_tracks

load_dotenv()

CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI",
                              "http://127.0.0.1:8050/callback")
SCOPE = ("streaming,user-read-email,user-read-private,user-library-read,"
         "playlist-read-private,playlist-read-collaborative")
# Where spotipy persists the OAuth token. In a container point this at
# a mounted volume so a redeploy keeps the operator signed in.
CACHE_PATH = os.environ.get("SPOTIFY_CACHE_PATH", ".cache")

oauth = SpotifyOAuth(client_id=CLIENT_ID,
                     client_secret=CLIENT_SECRET,
                     redirect_uri=REDIRECT_URI,
                     scope=SCOPE,
                     cache_path=CACHE_PATH)


def get_spotify():
    """Return an authed spotipy client, or None if not usable.

    validate_token refreshes an expired token *and* rejects a cached
    token whose granted scopes are a subset of the ones we now require.
    That last part matters: when SCOPE grows, a token minted under the
    old scopes would otherwise keep working silently and the API would
    just hide the newly-permitted data (e.g. private playlists). By
    returning None instead, the UI tells the operator to reconnect.

    A single operator drives one player, so the shared cache suffices.
    """
    token_info = oauth.validate_token(oauth.cache_handler.get_cached_token())
    if not token_info:
        return None
    return spotipy.Spotify(auth=token_info["access_token"])


def control_player(action, device_id, uri=None, position_ms=0):
    """Apply a playback action through the Spotify Web API.

    Callers only express intent: "play_uri", "resume", "pause" or
    "seek". play_uri honours position_ms so a per-song start offset
    skips slow intros. Returns True when the command was issued, False
    if it could not be (not authenticated, no active device, or the API
    rejected it).
    """
    if not device_id:
        return False
    sp = get_spotify()
    if sp is None:
        return False
    try:
        if action == "play_uri":
            sp.start_playback(device_id=device_id, uris=[uri],
                              position_ms=position_ms or 0)
        elif action == "resume":
            sp.start_playback(device_id=device_id)
        elif action == "pause":
            sp.pause_playback(device_id=device_id)
        elif action == "seek":
            sp.seek_track(int(position_ms or 0), device_id=device_id)
        else:
            return False
        return True
    except spotipy.SpotifyException as exc:
        print(f"Spotify playback error ({action}): {exc}")
        return False


def fetch_all_playlist_tracks(sp, playlist_id, cap=300):
    """Page through a playlist and return its playable track objects.

    Capped so a pathologically large playlist cannot stall the UI.
    """
    page = sp.playlist_items(playlist_id, limit=100)
    tracks = extract_playlist_tracks(page["items"])
    while page.get("next") and len(tracks) < cap:
        page = sp.next(page)
        tracks.extend(extract_playlist_tracks(page["items"]))
    return tracks[:cap]
