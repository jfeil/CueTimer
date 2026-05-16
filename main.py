import dash
from dash import Input, Output, dcc, html, State
import dash_bootstrap_components as dbc
import spotipy
from spotipy import SpotifyOAuth
from flask import Flask, request, redirect, session
import os

from queue_logic import (track_to_item, find_entry, step_queue,
                          with_new_row_id, reorder_queue, set_start_ms,
                          clamp_start_ms)
from timer_logic import next_music_state, progress_percent
from spotify_ids import parse_playlist_id, extract_playlist_tracks

from dotenv import load_dotenv

load_dotenv()

# --- Configuration ---
CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8050/callback")
SCOPE = ("streaming,user-read-email,user-read-private,user-library-read,"
         "playlist-read-private,playlist-read-collaborative")

server = Flask(__name__)
server.secret_key = os.urandom(24)

# Spotipy OAuth
oauth = SpotifyOAuth(client_id=CLIENT_ID,
                     client_secret=CLIENT_SECRET,
                     redirect_uri=REDIRECT_URI,
                     scope=SCOPE)


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

    Hides auth, device targeting and API error handling so callers only
    express intent: "play_uri", "resume", "pause" or "seek". play_uri
    honours position_ms so a per-song start offset skips slow intros.
    Returns True when the command was issued, False if it could not be
    (not authenticated, no active device, or the API rejected it).
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

dbc_css = "https://cdn.jsdelivr.net/gh/AnnMarieW/dash-bootstrap-templates/dbc.min.css"
app = dash.Dash(__name__, server=server,
                external_scripts=[
                    "https://sdk.scdn.co/spotify-player.js",
                    "https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js",
                ],
                external_stylesheets=[dbc.themes.DARKLY, dbc.icons.BOOTSTRAP, dbc_css],
                suppress_callback_exceptions=True,
                meta_tags=[
                    {"name": "viewport", "content": "width=device-width, initial-scale=1"}
                ])

default_time = 200
default_musik = 10


app.layout = dbc.Container([
    html.H1("Fussball Timer"),
    dcc.Interval(id="interval", interval=1000),
    dcc.Store(id="spotify-status"),
    dcc.Store(id="spotify-ts"),
    dcc.Store(id="data_memory", storage_type="memory",
              data={"running": False, "max_time": 0, "music_phase": "idle"}),
    dcc.Store(id="timer_memory", storage_type="memory", data=default_time),
    dcc.Store(id="device-id", storage_type="memory"),
    dcc.Store(id="sdk-state", storage_type="memory"),
    dcc.Store(id="music-command", storage_type="memory"),
    dcc.Store(id="search-store", storage_type="memory", data=[]),
    dcc.Store(id="playlist-tracks-store", storage_type="memory", data=[]),
    dcc.Store(id="queue-order", storage_type="memory"),
    dcc.Store(id="sortable-init", storage_type="memory"),
    dcc.Store(id="position-sync", storage_type="memory"),
    dcc.Store(id="seek-sink", storage_type="memory"),
    dcc.Store(id="nowplaying", storage_type="memory",
              data={"rowId": None, "uri": None}),
    dcc.Store(id="queue-store", storage_type="local", data=[]),
    dcc.Store(id="data_persistent", storage_type="local"),
    html.Br(),
    dbc.Container([dbc.Progress(id="timer_progress", value=100, style={"height": "30px"})]),
    html.Br(),
    dbc.Container([dbc.Label("Timer"), dbc.Input(id="timer_data", type="number", value=default_time)]),
    dbc.Container([dbc.Label("Musik ab"), dbc.Input(id="musik_start", type="number", value=default_musik)]),
    html.Br(),
    dbc.Container([dbc.ButtonGroup([
        dbc.Button("Reset", id="reset_button", color="danger"),
        dbc.Button("Start", id="start_button", color="success"),
    ])]),
    html.Br(),
    dbc.Container([
    html.Div(id="main", children=[
        dbc.Button("Connect to Spotify", id="connect-button"),
        html.H4(id="player-state-heading"),
        html.Div(id="player-status"),
        html.Div([
            dbc.Button("⏮️", id="prev-btn"),
            dbc.Button("▶️", id="play-btn"),
            dbc.Button("⏸️", id="pause-btn"),
            dbc.Button("⏭️", id="next-btn"),
        ], id="player-controls", style={"marginTop": "1rem",
                                        "display": "none"}),
    ]),
    html.Div(id="track-info"),
    html.Div([
        dcc.Slider(id="position-slider", min=0, max=1, step=1000, value=0,
                   marks=None, updatemode="mouseup",
                   tooltip={"placement": "bottom"}),
        html.Small("0:00 / 0:00", id="position-label",
                   className="text-muted"),
    ], className="mt-2"),
    html.Hr(),
    dbc.Container([
        html.H4("Songs suchen"),
        dbc.InputGroup([
            dbc.Input(id="search-input", placeholder="Titel oder Künstler…",
                      debounce=True),
            dbc.Button("Suchen", id="search-btn", color="primary"),
        ]),
        dbc.ListGroup(id="search-results", className="mt-2"),
    ]),
    html.Hr(),
    dbc.Container([
        html.H4("Playlist hinzufügen"),
        dbc.InputGroup([
            dbc.Select(id="playlist-select", placeholder="Eigene Playlist…"),
            dbc.Button("Playlists laden", id="load-playlists-btn",
                       color="secondary", outline=True),
        ], className="mb-2"),
        dbc.InputGroup([
            dbc.Input(id="playlist-url",
                      placeholder="…oder Playlist-Link / URI einfügen"),
            dbc.Button("Laden", id="playlist-load-btn", color="primary"),
        ]),
        dbc.Stack([
            html.Div(id="playlist-status", className="text-muted me-auto"),
            dbc.Button("Alle / keine", id="playlist-toggle-all",
                       size="sm", color="secondary", outline=True),
        ], direction="horizontal", gap=2, className="mt-2"),
        dbc.Checklist(id="playlist-track-checklist", options=[], value=[],
                      className="mt-2"),
        dbc.ButtonGroup([
            dbc.Button("Auswahl hinzufügen", id="playlist-add-sel-btn",
                       color="success", outline=True),
            dbc.Button("Alle hinzufügen", id="playlist-add-all-btn",
                       color="success"),
        ], className="mt-2"),
    ]),
    html.Hr(),
    dbc.Container([
        dbc.Stack([
            html.H4("Warteschlange", className="me-auto"),
            dbc.Button("Leeren", id="queue-clear", color="secondary",
                       size="sm", outline=True),
        ], direction="horizontal", gap=2),
        dbc.ListGroup(id="spotify-tracks", className="mt-2"),
    ])])
])

app.clientside_callback(
    """
    function(n, status) {
        const ts = Date.now();
        if (!status || status.state != window._spotify_status){
            return {state: window._spotify_status, ts: ts};
        } else {
            return status
        }
    }
    """,
    Output("spotify-status", "data"),
    Input("interval", "n_intervals"),
    State("spotify-status", "data"),
)

app.clientside_callback(
    """
    function(n, current) {
        const s = window._spotify_playstate || null;
        if (!s) return window.dash_clientside.no_update;
        if (current && current.uri === s.uri && current.paused === s.paused
            && current.ended === s.ended) {
            return window.dash_clientside.no_update;
        }
        return s;
    }
    """,
    Output("sdk-state", "data"),
    Input("interval", "n_intervals"),
    State("sdk-state", "data"),
)

app.clientside_callback(
    """
    function(n, current) {
        const id = window._spotify_device_id || null;
        return (id === current) ? window.dash_clientside.no_update : id;
    }
    """,
    Output("device-id", "data"),
    Input("interval", "n_intervals"),
    State("device-id", "data"),
)

# Smooth playback position: the SDK only emits on play/pause/seek/track
# change, so between events we interpolate from the last reported
# position plus elapsed wall-clock (only while not paused). The same
# value is mirrored into position-sync so the seek callback can tell a
# user drag apart from this once-a-second programmatic update.
app.clientside_callback(
    """
    function(n) {
        const s = window._spotify_playstate;
        if (!s) return [window.dash_clientside.no_update,
                         window.dash_clientside.no_update,
                         window.dash_clientside.no_update,
                         window.dash_clientside.no_update];
        const elapsed = s.paused ? 0 : (Date.now() - s.ts);
        const pos = Math.max(0, Math.min(s.duration, s.position + elapsed));
        const fmt = ms => {
            const t = Math.round(ms / 1000);
            return Math.floor(t / 60) + ":" + String(t % 60).padStart(2, "0");
        };
        return [Math.max(1, s.duration), pos,
                fmt(pos) + " / " + fmt(s.duration), pos];
    }
    """,
    Output("position-slider", "max"),
    Output("position-slider", "value"),
    Output("position-label", "children"),
    Output("position-sync", "data"),
    Input("interval", "n_intervals"),
)


@app.callback(
    Output("seek-sink", "data"),
    Input("position-slider", "value"),
    State("position-sync", "data"),
    State("device-id", "data"),
    prevent_initial_call=True,
)
def seek_position(value, synced, device_id):
    """Seek only when the operator actually moved the slider.

    The interpolation callback sets value and position-sync to the same
    number every tick, so a near-match means this is that programmatic
    update, not a drag.
    """
    if value is None or synced is None:
        return dash.no_update
    if abs(value - synced) <= 2000:
        return dash.no_update
    control_player("seek", device_id, position_ms=value)
    return dash.no_update


def _fmt_duration(ms):
    s = round(ms / 1000)
    return f"{s // 60}:{s % 60:02d}"


@app.callback(
    Output("spotify-tracks", "children"),
    Input("queue-store", "data"),
)
def render_queue(queue):
    if not queue:
        return dbc.ListGroupItem("Warteschlange ist leer.", color="dark")
    items = []
    for idx, t in enumerate(queue):
        thumb = html.Img(src=t["img"], height="40px",
                         className="me-2 rounded") if t.get("img") else None
        items.append(dbc.ListGroupItem([
            dbc.Stack([
                html.Span(f"⠿ {idx + 1}.", className="drag-handle text-muted me-2",
                          style={"cursor": "grab", "touchAction": "none"},
                          title="Ziehen zum Sortieren"),
                thumb,
                html.Div([
                    html.Div(t["name"], className="fw-bold"),
                    html.Small(t["artist"], className="text-muted"),
                ], className="me-auto"),
                html.Small(_fmt_duration(t["duration_ms"]),
                           className="text-muted me-2"),
                dbc.Button("▶", id={"type": "queue-play", "row": t["rowId"]},
                           size="sm", color="success", outline=True,
                           title="Jetzt abspielen"),
                dbc.Button("✕", id={"type": "queue-remove", "row": t["rowId"]},
                           size="sm", color="danger", outline=True,
                           title="Entfernen"),
            ], direction="horizontal", gap=2),
            html.Div([
                html.Small(f"Start ab {_fmt_duration(t.get('start_ms', 0))}",
                           className="text-muted"),
                dcc.Slider(
                    id={"type": "queue-start", "row": t["rowId"]},
                    min=0, max=max(t["duration_ms"], 1000), step=1000,
                    value=t.get("start_ms", 0), marks=None,
                    tooltip={"placement": "bottom"},
                ),
            ], className="mt-1"),
        ], id={"type": "queue-row", "row": t["rowId"]}))
    return items


@app.callback(
    Output("queue-store", "data", allow_duplicate=True),
    Input({"type": "queue-start", "row": dash.ALL}, "value"),
    State("queue-store", "data"),
    prevent_initial_call=True,
)
def set_queue_start(_values, queue):
    """Persist a row's start offset when its slider is released."""
    trigger = dash.callback_context.triggered_id
    if not trigger:
        return dash.no_update
    new_value = dash.callback_context.triggered[0]["value"]
    if new_value is None:
        return dash.no_update
    entry = find_entry(queue, trigger["row"])
    if entry is None:
        return dash.no_update
    # No-op when unchanged, otherwise re-rendering the slider would
    # retrigger this callback forever.
    if clamp_start_ms(new_value, entry["duration_ms"]) == \
            entry.get("start_ms", 0):
        return dash.no_update
    return set_start_ms(queue, trigger["row"], new_value)


# Re-arm SortableJS every time the queue list is re-rendered (Dash
# replaces the DOM nodes, destroying any prior Sortable instance).
app.clientside_callback(
    """
    function(children) {
        setTimeout(window.initQueueSortable, 0);
        return window.dash_clientside.no_update;
    }
    """,
    Output("sortable-init", "data"),
    Input("spotify-tracks", "children"),
)


@app.callback(
    Output("queue-store", "data", allow_duplicate=True),
    Input("queue-order", "data"),
    State("queue-store", "data"),
    prevent_initial_call=True,
)
def apply_queue_order(order, queue):
    """Rearrange the queue to match an order produced by a drag."""
    if not order or not order.get("ids"):
        return dash.no_update
    return reorder_queue(queue, order["ids"])


@app.callback(
    Output("track-info", "children"),
    Input("nowplaying", "data"),
    State("queue-store", "data"),
)
def render_now_playing(nowplaying, queue):
    """Show the selected song straight from nowplaying.

    Driven by the pointer (not the SDK), so it updates the instant
    next/prev move the selection even when no audio is playing yet.
    """
    entry = find_entry(queue, (nowplaying or {}).get("rowId"))
    if entry is None:
        return html.Span("Kein Titel ausgewählt.", className="text-muted")
    cover = html.Img(src=entry["img"], height="64px",
                     className="me-2 rounded") if entry.get("img") else None
    return dbc.Stack([
        cover,
        html.Div([
            html.Div(entry["name"], className="fw-bold"),
            html.Small(entry["artist"], className="text-muted"),
        ]),
    ], direction="horizontal", gap=2)


@app.callback(
    Output("queue-store", "data", allow_duplicate=True),
    Input({"type": "queue-remove", "row": dash.ALL}, "n_clicks"),
    State("queue-store", "data"),
    prevent_initial_call=True,
)
def remove_from_queue(_clicks, queue):
    trig = dash.callback_context.triggered_id
    if not trig or not any(_clicks):
        return dash.no_update
    return [t for t in (queue or []) if t["rowId"] != trig["row"]]


@app.callback(
    Output("queue-store", "data", allow_duplicate=True),
    Input("queue-clear", "n_clicks"),
    prevent_initial_call=True,
)
def clear_queue(n):
    if not n:
        return dash.no_update
    return []


def _search_row(idx, item):
    """One search-result row: thumb, title/artist and an add button."""
    thumb = html.Img(src=item["img"], height="40px",
                     className="me-2 rounded") if item.get("img") else None
    return dbc.ListGroupItem(dbc.Stack([
        thumb,
        html.Div([
            html.Div(item["name"], className="fw-bold"),
            html.Small(item["artist"], className="text-muted"),
        ], className="me-auto"),
        dbc.Button("+ Warteschlange",
                   id={"type": "search-add", "idx": idx},
                   size="sm", color="success", outline=True),
    ], direction="horizontal", gap=2))


@app.callback(
    Output("search-store", "data"),
    Output("search-results", "children"),
    Input("search-btn", "n_clicks"),
    Input("search-input", "n_submit"),
    State("search-input", "value"),
    prevent_initial_call=True,
)
def do_search(_clicks, _submit, query):
    query = (query or "").strip()
    if not query:
        return [], dbc.ListGroupItem("Bitte einen Suchbegriff eingeben.",
                                     color="dark")
    sp = get_spotify()
    if sp is None:
        return [], dbc.ListGroupItem("Bitte zuerst mit Spotify verbinden.",
                                     color="warning")
    try:
        results = sp.search(q=query, type="track", limit=10)
    except spotipy.SpotifyException as exc:
        return [], dbc.ListGroupItem(f"Suche fehlgeschlagen: {exc}",
                                     color="danger")

    items = [track_to_item(t) for t in results["tracks"]["items"]]
    if not items:
        return [], dbc.ListGroupItem("Keine Treffer.", color="dark")
    return items, [_search_row(i, it) for i, it in enumerate(items)]


@app.callback(
    Output("queue-store", "data", allow_duplicate=True),
    Input({"type": "search-add", "idx": dash.ALL}, "n_clicks"),
    State("search-store", "data"),
    State("queue-store", "data"),
    prevent_initial_call=True,
)
def add_search_result(_clicks, results, queue):
    trigger = dash.callback_context.triggered_id
    if not trigger or not any(_clicks):
        return dash.no_update
    idx = trigger["idx"]
    if idx >= len(results or []):
        return dash.no_update
    return (queue or []) + [with_new_row_id(results[idx])]


def _fetch_all_playlist_tracks(sp, playlist_id, cap=300):
    """Page through a playlist and return its playable track objects.

    Capped so a pathologically large playlist cannot stall the UI.
    """
    page = sp.playlist_items(playlist_id, limit=100)
    tracks = extract_playlist_tracks(page["items"])
    while page.get("next") and len(tracks) < cap:
        page = sp.next(page)
        tracks.extend(extract_playlist_tracks(page["items"]))
    return tracks[:cap]


@app.callback(
    Output("playlist-select", "options"),
    Output("playlist-status", "children", allow_duplicate=True),
    Input("load-playlists-btn", "n_clicks"),
    prevent_initial_call=True,
)
def load_user_playlists(_clicks):
    sp = get_spotify()
    if sp is None:
        return [], "Bitte zuerst mit Spotify verbinden."
    try:
        playlists = sp.current_user_playlists(limit=50)["items"]
    except spotipy.SpotifyException as exc:
        return [], f"Playlists konnten nicht geladen werden: {exc}"
    options = [{"label": p["name"], "value": p["id"]}
               for p in playlists if p]
    return options, f"{len(options)} Playlists geladen."


@app.callback(
    Output("playlist-tracks-store", "data"),
    Output("playlist-track-checklist", "options"),
    Output("playlist-track-checklist", "value"),
    Output("playlist-status", "children", allow_duplicate=True),
    Input("playlist-select", "value"),
    Input("playlist-load-btn", "n_clicks"),
    State("playlist-url", "value"),
    prevent_initial_call=True,
)
def load_playlist_tracks(selected_id, _clicks, url):
    trigger = dash.callback_context.triggered_id
    if trigger == "playlist-load-btn":
        playlist_id = parse_playlist_id(url)
        if not playlist_id:
            return [], [], [], "Kein gültiger Playlist-Link."
    else:
        playlist_id = selected_id
    if not playlist_id:
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update

    sp = get_spotify()
    if sp is None:
        return [], [], [], "Bitte zuerst mit Spotify verbinden."
    try:
        raw_tracks = _fetch_all_playlist_tracks(sp, playlist_id)
    except spotipy.SpotifyException as exc:
        return [], [], [], f"Playlist konnte nicht geladen werden: {exc}"

    items = [track_to_item(t) for t in raw_tracks]
    options = [{"label": f"{it['name']} — {it['artist']}",
                "value": it["rowId"]} for it in items]
    value = [it["rowId"] for it in items]
    status = (f"{len(items)} Titel geladen — alle ausgewählt."
              if items else "Playlist enthält keine spielbaren Titel.")
    return items, options, value, status


@app.callback(
    Output("playlist-track-checklist", "value", allow_duplicate=True),
    Input("playlist-toggle-all", "n_clicks"),
    State("playlist-track-checklist", "value"),
    State("playlist-tracks-store", "data"),
    prevent_initial_call=True,
)
def toggle_all_playlist_tracks(_clicks, selected, playlist_items):
    """Flip the checklist between all selected and none."""
    all_ids = [it["rowId"] for it in (playlist_items or [])]
    if selected and len(selected) == len(all_ids):
        return []
    return all_ids


@app.callback(
    Output("queue-store", "data", allow_duplicate=True),
    Input("playlist-add-sel-btn", "n_clicks"),
    Input("playlist-add-all-btn", "n_clicks"),
    State("playlist-track-checklist", "value"),
    State("playlist-tracks-store", "data"),
    State("queue-store", "data"),
    prevent_initial_call=True,
)
def add_playlist_tracks(_sel_clicks, _all_clicks, selected_ids,
                        playlist_items, queue):
    trigger = dash.callback_context.triggered_id
    if trigger == "playlist-add-all-btn":
        chosen = playlist_items or []
    elif trigger == "playlist-add-sel-btn":
        wanted = set(selected_ids or [])
        chosen = [it for it in (playlist_items or [])
                  if it["rowId"] in wanted]
    else:
        return dash.no_update
    if not chosen:
        return dash.no_update
    return (queue or []) + [with_new_row_id(it) for it in chosen]

@app.callback(
    Output("spotify-ts", "data"),
    Output("connect-button", "style"),
    Output("player-state-heading", "children"),
    Output("player-status", "children"),
    Output("player-controls", "style"),
    State("spotify-ts", "data"),
    Input("spotify-status", "data"),
)
def update_main_layout(ts, status):
    """Reflect the auth/player state without rebuilding the DOM.

    Every control keeps a stable id in the layout; this only toggles
    visibility and status text, so callbacks that target the transport
    buttons always resolve.
    """
    if ts and ts >= status["ts"]:
        return ts, dash.no_update, dash.no_update, dash.no_update, dash.no_update

    shown = {"marginTop": "1rem"}
    hidden = {"display": "none"}

    if status["state"] == "authenticated":
        return (status["ts"], hidden, "✅ Authenticated with Spotify",
                "Warte auf den Player…", hidden)
    if status["state"] == "player-ready":
        return (status["ts"], hidden, "🎵 Spotify Web Player verbunden",
                "Player ist bereit", shown)
    return status["ts"], {}, "", "", hidden

def _np(entry):
    """Build a now-playing record (the current song pointer)."""
    return {"rowId": entry["rowId"], "uri": entry["uri"]}


def _audio_playing(sdk_state):
    """True when Spotify currently has audio running."""
    return bool(sdk_state and sdk_state.get("uri")
                and not sdk_state.get("paused"))


def _start(entry, sdk_state, device_id):
    """Best-effort play of an entry. If Spotify already has this exact
    track loaded we resume it (the player keeps the position, 0s or
    20s); otherwise we start it fresh. Playback is a side effect — the
    caller updates the selection regardless of whether it lands."""
    if sdk_state and sdk_state.get("uri") == entry["uri"]:
        control_player("resume", device_id)
    else:
        control_player("play_uri", device_id, entry["uri"],
                       position_ms=entry.get("start_ms", 0))


@app.callback(
    Output("nowplaying", "data", allow_duplicate=True),
    Input("play-btn", "n_clicks"),
    Input("pause-btn", "n_clicks"),
    Input("next-btn", "n_clicks"),
    Input("prev-btn", "n_clicks"),
    Input({"type": "queue-play", "row": dash.ALL}, "n_clicks"),
    State("queue-store", "data"),
    State("device-id", "data"),
    State("nowplaying", "data"),
    State("sdk-state", "data"),
    prevent_initial_call=True,
)
def playback_controls(_play_c, _pause_c, _next_c, _prev_c, row_clicks,
                      queue, device_id, nowplaying, sdk_state):
    """Translate transport buttons into a single playback intent.

    next/prev play the new song only when audio is currently playing;
    otherwise they just move the pointer (no audio) so the operator can
    pre-set what plays next.
    """
    trigger = dash.callback_context.triggered_id
    if trigger is None:
        return dash.no_update
    current_row = (nowplaying or {}).get("rowId")

    if trigger == "pause-btn":
        control_player("pause", device_id)
        return dash.no_update

    if trigger == "play-btn":
        entry = find_entry(queue, current_row) or step_queue(queue, None,
                                                             "first")
        if entry is None:
            return dash.no_update
        _start(entry, sdk_state, device_id)
        return _np(entry)

    if trigger == "next-btn":
        target = step_queue(queue, current_row, "next")
    elif trigger == "prev-btn":
        target = step_queue(queue, current_row, "prev")
    elif isinstance(trigger, dict) and trigger.get("type") == "queue-play":
        target = find_entry(queue, trigger["row"]) if any(row_clicks) else None
    else:
        return dash.no_update

    if target is None:
        return dash.no_update

    # The selection always moves; audio only follows when something is
    # already playing (next/prev) or it was an explicit queue play.
    if trigger != "next-btn" and trigger != "prev-btn":
        _start(target, sdk_state, device_id)            # queue ▶
    elif _audio_playing(sdk_state):
        _start(target, sdk_state, device_id)
    return _np(target)


@app.callback(
    Output("nowplaying", "data", allow_duplicate=True),
    Input("sdk-state", "data"),
    State("queue-store", "data"),
    State("device-id", "data"),
    State("nowplaying", "data"),
    prevent_initial_call=True,
)
def auto_advance(sdk_state, queue, device_id, nowplaying):
    """Advance to the next queue entry when the current track ends."""
    if not sdk_state or not sdk_state.get("ended"):
        return dash.no_update
    current_row = (nowplaying or {}).get("rowId")
    target = step_queue(queue, current_row, "next")
    if target is None:
        return dash.no_update
    _start(target, sdk_state, device_id)
    return _np(target)


@app.callback(
    Output("nowplaying", "data", allow_duplicate=True),
    Input("music-command", "data"),
    State("queue-store", "data"),
    State("device-id", "data"),
    State("nowplaying", "data"),
    State("sdk-state", "data"),
    prevent_initial_call=True,
)
def apply_music_command(cmd, queue, device_id, nowplaying, sdk_state):
    """Execute the timer's music intent against the player.

    "pause" stops playback. "play" starts the selected song (resuming
    it if Spotify already has it loaded), or the first queue entry when
    nothing is selected.
    """
    if not cmd:
        return dash.no_update
    action = cmd.get("command")
    entry = find_entry(queue, (nowplaying or {}).get("rowId"))

    if action == "pause":
        control_player("pause", device_id)
        return dash.no_update

    if action == "play":
        entry = entry or step_queue(queue, None, "first")
        if entry is None:
            return dash.no_update
        _start(entry, sdk_state, device_id)
        return _np(entry)

    return dash.no_update

# Flask route for /login
@server.route("/login")
def login():
    auth_url = oauth.get_authorize_url()
    return redirect(auth_url)

# Flask route for /callback
@server.route("/callback")
def callback():
    code = request.args.get('code')
    token_info = oauth.get_access_token(code, as_dict=True)
    session['token_info'] = token_info
    access_token = token_info['access_token']
    # Send token back to main window via JS postMessage
    return f"""
    <script>
        window.opener.postMessage({{ token: "{access_token}" }}, window.location.origin);
        window.close();
    </script>
    """


@app.callback(Output("data_memory", "data"),
              Output("start_button", "children"),
              Output("timer_memory", "data"),
              Output("timer_progress", "label"),
              Output("music-command", "data"),
              State("data_memory", "data"),
              State("timer_data", "value"),
              State("timer_memory", "data"),
              State("start_button", "children"),
              State("musik_start", "value"),
              Input("start_button", "n_clicks"),
              Input("reset_button", "n_clicks"),
              Input("interval", "n_intervals"))
def update_timer(data, max_time, current_timer, button_label, music_start,
                 _start_clicks, _reset_clicks, n_intervals):
    timer_interval = current_timer
    label_button = button_label
    trigger = dash.callback_context.triggered[0]["prop_id"]
    music_event = None

    if trigger == "reset_button.n_clicks":
        timer_interval = max_time
        music_event = "reset"
    elif trigger == "start_button.n_clicks":
        if button_label == "Start":
            if timer_interval <= 0:
                timer_interval = max_time
            data["running"] = True
            label_button = "Stop"
            music_event = "start_match"
        else:
            data["running"] = False
            label_button = "Start"
    elif trigger == "interval.n_intervals" and data["running"]:
        timer_interval -= 1
        music_event = "tick"

    timer_interval = max(timer_interval, 0)
    data["max_time"] = max_time

    phase = data.get("music_phase", "idle")
    new_phase, command = next_music_state(phase, timer_interval,
                                          music_start, music_event)
    data["music_phase"] = new_phase
    music_command = ({"command": command, "ts": n_intervals or 0}
                     if command else dash.no_update)

    return data, label_button, timer_interval, timer_interval, music_command


@app.callback(
    Output("timer_progress", "value"),
    State("data_memory", "data"),
    Input("timer_memory", "data"))
def update_progressbar(data, current_timer):
    return progress_percent(current_timer, data.get("max_time", 0))


if __name__ == '__main__':
    app.run(debug=True)