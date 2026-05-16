import dash
from dash import Input, Output, dcc, html, State
import dash_bootstrap_components as dbc
import spotipy
from spotipy import SpotifyOAuth
from flask import Flask, request, redirect
import os
import json

from queue_logic import (track_to_item, find_entry, step_queue,
                          with_new_row_id, reorder_queue, set_start_ms,
                          clamp_start_ms, played_split)
from timer_logic import next_music_state, progress_percent, format_clock
from spotify_ids import parse_playlist_id, extract_playlist_tracks

from dotenv import load_dotenv

load_dotenv()

# --- Configuration ---
CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8050/callback")
SCOPE = ("streaming,user-read-email,user-read-private,user-library-read,"
         "playlist-read-private,playlist-read-collaborative")
# Where spotipy persists the OAuth token. In a container point this at
# a mounted volume so a redeploy keeps the operator signed in.
CACHE_PATH = os.environ.get("SPOTIFY_CACHE_PATH", ".cache")

server = Flask(__name__)
# A stable key keeps Flask sessions valid across restarts/workers;
# falls back to ephemeral when unset (fine for local single use).
server.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24)

# Spotipy OAuth
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
                title="Cue Timer",
                update_title=None,
                external_scripts=[
                    "https://sdk.scdn.co/spotify-player.js",
                    "https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js",
                ],
                external_stylesheets=[dbc.themes.DARKLY, dbc.icons.BOOTSTRAP, dbc_css],
                suppress_callback_exceptions=True,
                meta_tags=[
                    {"name": "viewport", "content": "width=device-width, initial-scale=1"}
                ])

# Use the bundled SVG favicon instead of Dash's default.
app.index_string = """<!DOCTYPE html>
<html>
<head>
{%metas%}
<title>{%title%}</title>
<link rel="icon" type="image/svg+xml" href="/assets/icon.svg">
{%css%}
</head>
<body>
{%app_entry%}
<footer>{%config%}{%scripts%}{%renderer%}</footer>
</body>
</html>"""

default_time = 200
default_musik = 10


app.layout = dbc.Container([
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
    html.H1("Cue Timer", className="text-center my-4"),

    dbc.Card([
        dbc.CardHeader("Spiel-Timer"),
        dbc.CardBody([
            dbc.Progress(id="timer_progress", value=100,
                         className="mb-3", style={"height": "28px"}),
            dbc.Row([
                dbc.Col([
                    dbc.Label("Spieldauer (Sekunden)"),
                    dbc.Input(id="timer_data", type="number",
                              value=default_time),
                ], md=6),
                dbc.Col([
                    dbc.Label("Musik ab (Sek. Restzeit)"),
                    dbc.Input(id="musik_start", type="number",
                              value=default_musik),
                    dbc.Checkbox(id="new-song-each",
                                 label="Neuer Song für jeden Countdown",
                                 value=False, class_name="mt-2"),
                ], md=6),
            ], class_name="g-3"),
            html.Div(dbc.ButtonGroup([
                dbc.Button([html.I(className="bi bi-arrow-counterclockwise me-2"),
                            "Reset"], id="reset_button", color="danger",
                           outline=True),
                dbc.Button([html.I(className="bi bi-play-fill me-2"), "Start"],
                           id="start_button", color="success"),
            ]), className="d-flex justify-content-center mt-3"),
        ]),
    ], class_name="mb-4"),

    html.Div(id="connect-section", children=dbc.Card(dbc.CardBody([
        html.P("Verbinde ein Spotify-Konto (Premium), um Musik zu "
               "steuern.", className="text-muted mb-3"),
        dbc.Button([html.I(className="bi bi-spotify me-2"),
                    "Mit Spotify verbinden"], id="connect-button",
                   color="success", size="lg"),
        html.Div(id="status-message", className="mt-3 text-muted"),
    ])), className="mb-4"),

    html.Div(id="spotify-ui", style={"display": "none"}, children=[
        dbc.Card([
            dbc.CardHeader("Player"),
            dbc.CardBody([
                html.Div(id="track-info", className="mb-3"),
                dcc.Slider(id="position-slider", min=0, max=1, step=1000,
                           value=0, marks=None, updatemode="mouseup",
                           tooltip={"placement": "bottom",
                                    "transform": "msClock"}),
                html.Small("0:00 / 0:00", id="position-label",
                           className="text-muted"),
                html.Div(dbc.ButtonGroup([
                    dbc.Button(html.I(className="bi bi-skip-start-fill"),
                               id="prev-btn", color="light",
                               outline=True, title="Vorheriger Titel"),
                    dbc.Button(html.I(className="bi bi-play-fill"),
                               id="playpause-btn", color="primary",
                               title="Wiedergabe / Pause"),
                    dbc.Button(html.I(className="bi bi-skip-end-fill"),
                               id="next-btn", color="light",
                               outline=True, title="Nächster Titel"),
                ], size="lg"), className="d-flex justify-content-center mt-3"),
            ]),
        ], class_name="mb-4"),

        dbc.Card([
            dbc.CardHeader("Songs suchen"),
            dbc.CardBody([
                dbc.InputGroup([
                    dbc.Input(id="search-input",
                              placeholder="Titel oder Künstler…",
                              debounce=True),
                    dbc.Button([html.I(className="bi bi-search me-2"),
                                "Suchen"], id="search-btn", color="primary"),
                ]),
                dbc.ListGroup(id="search-results", className="mt-3"),
            ]),
        ], class_name="mb-4"),

        dbc.Card([
            dbc.CardHeader(dbc.Stack([
                dbc.Button([html.I(className="bi bi-chevron-expand me-2"),
                            "Warteschlange"], id="queue-collapse-toggle",
                           color="link",
                           class_name="text-reset text-decoration-none "
                                       "p-0 fw-bold me-auto"),
                dbc.Button([html.I(className="bi bi-trash me-2"), "Leeren"],
                           id="queue-clear", color="danger", size="sm",
                           outline=True),
            ], direction="horizontal", gap=2)),
            dbc.Collapse(
                dbc.CardBody(dbc.ListGroup(id="spotify-tracks", flush=True)),
                id="queue-collapse", is_open=True),
        ], class_name="mb-4"),

        dbc.Card([
            dbc.CardHeader(
                dbc.Button(
                    [html.I(className="bi bi-chevron-expand me-2"),
                     "Playlist hinzufügen"],
                    id="playlist-collapse-toggle", color="link",
                    class_name="text-reset text-decoration-none p-0 fw-bold"),
            ),
            dbc.Collapse(dbc.CardBody([
                dbc.Select(id="playlist-select",
                           placeholder="Eigene Playlist wählen…",
                           class_name="mb-2"),
                dbc.InputGroup([
                    dbc.Input(id="playlist-url",
                              placeholder="…oder Playlist-Link / URI"),
                    dbc.Button("Laden", id="playlist-load-btn",
                               color="primary"),
                ]),
                dbc.Stack([
                    html.Div(id="playlist-status",
                             className="text-muted me-auto"),
                    dbc.Button("Alle / keine", id="playlist-toggle-all",
                               size="sm", color="light", outline=True),
                    dbc.Button("Ansicht leeren", id="playlist-clear-view",
                               size="sm", color="light", outline=True),
                ], direction="horizontal", gap=2, class_name="mt-3"),
                dbc.Checklist(id="playlist-track-checklist", options=[],
                              value=[], className="mt-2"),
                dbc.ButtonGroup([
                    dbc.Button("Auswahl hinzufügen",
                               id="playlist-add-sel-btn", color="success",
                               outline=True),
                    dbc.Button("Alle hinzufügen", id="playlist-add-all-btn",
                               color="success"),
                ], class_name="mt-3"),
            ]), id="playlist-collapse", is_open=False),
        ], class_name="mb-4"),
    ]),
], style={"maxWidth": "760px"}, className="pb-5")

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

# Player position. While the selected song is the one Spotify has
# loaded we interpolate the SDK position (it only emits on play/pause/
# seek/track change, so between events we add elapsed wall-clock). When
# the song is only armed (skipped to, not yet playing) we instead
# preview its configured start offset, so the player bar matches where
# playback will begin. position-sync mirrors the shown value so the
# seek callback can tell a real drag from this programmatic update.
app.clientside_callback(
    """
    function(n, nowplaying, queue) {
        const fmt = ms => {
            const t = Math.round((ms || 0) / 1000);
            return Math.floor(t / 60) + ":" + String(t % 60).padStart(2, "0");
        };
        const s = window._spotify_playstate;
        const rowId = nowplaying && nowplaying.rowId;
        let entry = null;
        if (rowId && queue) {
            entry = queue.find(e => e.rowId === rowId) || null;
        }

        if (s && entry && s.uri === entry.uri) {
            const elapsed = s.paused ? 0 : (Date.now() - s.ts);
            const pos = Math.max(0, Math.min(s.duration,
                                            s.position + elapsed));
            return [Math.max(1, s.duration), pos,
                    fmt(pos) + " / " + fmt(s.duration), pos];
        }
        if (entry) {
            const start = entry.start_ms || 0;
            const dur = Math.max(1, entry.duration_ms || 1);
            return [dur, start, fmt(start) + " / " + fmt(dur), start];
        }
        if (s) {
            const elapsed = s.paused ? 0 : (Date.now() - s.ts);
            const pos = Math.max(0, Math.min(s.duration,
                                            s.position + elapsed));
            return [Math.max(1, s.duration), pos,
                    fmt(pos) + " / " + fmt(s.duration), pos];
        }
        return [1, 0, "0:00 / 0:00", 0];
    }
    """,
    Output("position-slider", "max"),
    Output("position-slider", "value"),
    Output("position-label", "children"),
    Output("position-sync", "data"),
    Input("interval", "n_intervals"),
    Input("nowplaying", "data"),
    State("queue-store", "data"),
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
    Input("nowplaying", "data"),
)
def render_queue(queue, nowplaying):
    """Show only what's still to come; the next title sits at spot 1.

    Played/current entries stay in queue-store (so prev brings them
    back) but are hidden here.
    """
    _played, upcoming = played_split(queue, (nowplaying or {}).get("rowId"))
    if not upcoming:
        return dbc.ListGroupItem("Keine weiteren Titel.", color="dark")
    items = []
    for idx, t in enumerate(upcoming):
        thumb = html.Img(src=t["img"], height="44px",
                         className="rounded") if t.get("img") else None
        items.append(dbc.ListGroupItem([
            dbc.Stack([
                html.Span([html.I(className="bi bi-grip-vertical"),
                           f" {idx + 1}"],
                          className="drag-handle text-muted",
                          style={"cursor": "grab", "touchAction": "none",
                                 "minWidth": "2.5rem"},
                          title="Ziehen zum Sortieren"),
                thumb,
                html.Div([
                    html.Div(t["name"], className="fw-bold text-truncate"),
                    html.Small(t["artist"], className="text-muted"),
                ], className="me-auto", style={"minWidth": "0"}),
                html.Small(_fmt_duration(t["duration_ms"]),
                           className="text-muted"),
                dbc.Button(html.I(className="bi bi-play-fill"),
                           id={"type": "queue-play", "row": t["rowId"]},
                           size="sm", color="success",
                           title="Jetzt abspielen"),
                dbc.Button(html.I(className="bi bi-x-lg"),
                           id={"type": "queue-remove", "row": t["rowId"]},
                           size="sm", color="danger", outline=True,
                           title="Entfernen"),
            ], direction="horizontal", gap=3),
            html.Div([
                html.Small(f"Start ab {_fmt_duration(t.get('start_ms', 0))}",
                           className="text-muted"),
                dcc.Slider(
                    id={"type": "queue-start", "row": t["rowId"]},
                    min=0, max=max(t["duration_ms"], 1000), step=1000,
                    value=t.get("start_ms", 0), marks=None,
                    tooltip={"placement": "bottom", "transform": "msClock"},
                ),
            ], className="mt-2"),
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
    State("nowplaying", "data"),
    prevent_initial_call=True,
)
def apply_queue_order(order, queue, nowplaying):
    """Reorder only the upcoming part; played entries stay put.

    The drag list only contains the visible (upcoming) rows, so the
    hidden played/current prefix is preserved ahead of the reordered
    tail.
    """
    if not order or not order.get("ids"):
        return dash.no_update
    played, upcoming = played_split(queue, (nowplaying or {}).get("rowId"))
    return played + reorder_queue(upcoming, order["ids"])


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
        return html.Div("Kein Titel ausgewählt.",
                        className="text-muted text-center py-3")
    cover = html.Img(src=entry["img"], height="72px",
                     className="rounded") if entry.get("img") else None
    return dbc.Stack([
        cover,
        html.Div([
            html.Div(entry["name"], className="fw-bold fs-5 text-truncate"),
            html.Div(entry["artist"], className="text-muted text-truncate"),
        ], style={"minWidth": "0"}),
    ], direction="horizontal", gap=3)


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


def _playlist_error_message(exc, playlist_id):
    """Turn a Spotify API error into something the operator can act on.

    Spotify-owned algorithmic/editorial playlists (the 37i9dQZF1…
    namespace: Daily Mix, Radio, Editorial) lost third-party Web API
    access in Nov 2024 and now 404, which is otherwise baffling.
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


@app.callback(
    Output("playlist-select", "options"),
    Output("playlist-status", "children", allow_duplicate=True),
    Input("spotify-status", "data"),
    prevent_initial_call=True,
)
def load_user_playlists(status):
    """Load the operator's playlists implicitly once the player is up."""
    if not status or status.get("state") != "player-ready":
        return dash.no_update, dash.no_update
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
    Output("playlist-collapse", "is_open"),
    Input("playlist-collapse-toggle", "n_clicks"),
    State("playlist-collapse", "is_open"),
    prevent_initial_call=True,
)
def toggle_playlist_collapse(_n, is_open):
    return not is_open


@app.callback(
    Output("queue-collapse", "is_open"),
    Input("queue-collapse-toggle", "n_clicks"),
    State("queue-collapse", "is_open"),
    prevent_initial_call=True,
)
def toggle_queue_collapse(_n, is_open):
    return not is_open


def _playlist_options(items):
    """Checklist options (label/value) for a list of queue entries."""
    return [{"label": f"{it['name']} — {it['artist']}",
             "value": it["rowId"]} for it in items]


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
        return [], [], [], _playlist_error_message(exc, playlist_id)

    items = [track_to_item(t) for t in raw_tracks]
    value = [it["rowId"] for it in items]
    status = (f"{len(items)} Titel geladen — alle ausgewählt."
              if items else "Playlist enthält keine spielbaren Titel.")
    return items, _playlist_options(items), value, status


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
    Output("playlist-tracks-store", "data", allow_duplicate=True),
    Output("playlist-track-checklist", "options", allow_duplicate=True),
    Output("playlist-track-checklist", "value", allow_duplicate=True),
    Output("playlist-status", "children", allow_duplicate=True),
    Input("playlist-add-sel-btn", "n_clicks"),
    Input("playlist-add-all-btn", "n_clicks"),
    State("playlist-track-checklist", "value"),
    State("playlist-tracks-store", "data"),
    State("queue-store", "data"),
    prevent_initial_call=True,
)
def add_playlist_tracks(_sel_clicks, _all_clicks, selected_ids,
                        playlist_items, queue):
    """Append chosen tracks to the queue and drop them from the view.

    Removing what was just added gives immediate visual feedback and
    stops the same tracks being queued twice.
    """
    trigger = dash.callback_context.triggered_id
    items = playlist_items or []
    if trigger == "playlist-add-all-btn":
        chosen = items
    elif trigger == "playlist-add-sel-btn":
        wanted = set(selected_ids or [])
        chosen = [it for it in items if it["rowId"] in wanted]
    else:
        return (dash.no_update,) * 5
    if not chosen:
        return (dash.no_update, dash.no_update, dash.no_update,
                dash.no_update, "Keine Titel ausgewählt.")

    chosen_ids = {it["rowId"] for it in chosen}
    remaining = [it for it in items if it["rowId"] not in chosen_ids]
    new_queue = (queue or []) + [with_new_row_id(it) for it in chosen]
    status = f"{len(chosen)} Titel zur Warteschlange hinzugefügt."
    return (new_queue, remaining, _playlist_options(remaining),
            [it["rowId"] for it in remaining], status)


@app.callback(
    Output("playlist-tracks-store", "data", allow_duplicate=True),
    Output("playlist-track-checklist", "options", allow_duplicate=True),
    Output("playlist-track-checklist", "value", allow_duplicate=True),
    Output("playlist-status", "children", allow_duplicate=True),
    Input("playlist-clear-view", "n_clicks"),
    prevent_initial_call=True,
)
def clear_playlist_view(_n):
    """Empty the loaded-playlist view without touching the queue."""
    return [], [], [], "Ansicht geleert."

@app.callback(
    Output("spotify-ts", "data"),
    Output("connect-section", "style"),
    Output("status-message", "children"),
    Output("spotify-ui", "style"),
    State("spotify-ts", "data"),
    Input("spotify-status", "data"),
)
def update_main_layout(ts, status):
    """Show the connect prompt or the full player, never both.

    Only toggles visibility/status text — every control keeps a stable
    id in the layout so its callbacks always resolve.
    """
    if not status:
        return dash.no_update, dash.no_update, dash.no_update, dash.no_update
    if ts and ts >= status["ts"]:
        return ts, dash.no_update, dash.no_update, dash.no_update

    visible = {}
    hidden = {"display": "none"}

    if status["state"] == "player-ready":
        return status["ts"], hidden, "", visible
    if status["state"] == "authenticated":
        return (status["ts"], visible,
                "Verbunden — warte auf den Web Player…", hidden)
    return status["ts"], visible, "", hidden

@app.callback(
    Output("playpause-btn", "children"),
    Input("sdk-state", "data"),
)
def render_playpause_icon(sdk_state):
    """Mirror the play/pause toggle to the actual player state."""
    icon = "bi bi-pause-fill" if _audio_playing(sdk_state) \
        else "bi bi-play-fill"
    return html.I(className=icon)


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
    Input("playpause-btn", "n_clicks"),
    Input("next-btn", "n_clicks"),
    Input("prev-btn", "n_clicks"),
    Input({"type": "queue-play", "row": dash.ALL}, "n_clicks"),
    State("queue-store", "data"),
    State("device-id", "data"),
    State("nowplaying", "data"),
    State("sdk-state", "data"),
    prevent_initial_call=True,
)
def playback_controls(_pp_c, _next_c, _prev_c, row_clicks,
                      queue, device_id, nowplaying, sdk_state):
    """Translate transport buttons into a single playback intent.

    play/pause is one toggle; next/prev play the new song only when
    audio is currently playing, otherwise they just move the pointer
    (no audio) so the operator can pre-set what plays next.
    """
    trigger = dash.callback_context.triggered_id
    if trigger is None:
        return dash.no_update
    current_row = (nowplaying or {}).get("rowId")

    if trigger == "playpause-btn":
        if _audio_playing(sdk_state):
            control_player("pause", device_id)
            return dash.no_update
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
    State("new-song-each", "value"),
    prevent_initial_call=True,
)
def apply_music_command(cmd, queue, device_id, nowplaying, sdk_state,
                        new_song_each):
    """Execute the timer's music intent against the player.

    "pause" stops playback. "play" starts the selected song (resuming
    it if Spotify already has it loaded). When "Neuer Song für jeden
    Countdown" is on it instead advances to the next queue entry, so a
    fresh song plays every time the countdown reaches "Musik ab". With
    nothing selected it starts the first entry.
    """
    if not cmd:
        return dash.no_update
    action = cmd.get("command")
    current_row = (nowplaying or {}).get("rowId")

    if action == "pause":
        control_player("pause", device_id)
        return dash.no_update

    if action == "play":
        if new_song_each:
            entry = step_queue(queue, current_row, "next")
        else:
            entry = find_entry(queue, current_row) \
                or step_queue(queue, None, "first")
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
    # JSON-encode the token so it cannot break out of the JS string,
    # and guard window.opener in case the popup was detached.
    payload = json.dumps({"token": token_info["access_token"]})
    return f"""
    <script>
        if (window.opener) {{
            window.opener.postMessage({payload}, window.location.origin);
        }}
        window.close();
    </script>
    """


def _start_button_label(running):
    """Icon + text for the Start/Stop toggle, mirroring its state."""
    if running:
        return [html.I(className="bi bi-pause-fill me-2"), "Stop"]
    return [html.I(className="bi bi-play-fill me-2"), "Start"]


@app.callback(Output("data_memory", "data"),
              Output("start_button", "children"),
              Output("timer_memory", "data"),
              Output("timer_progress", "label"),
              Output("music-command", "data"),
              State("data_memory", "data"),
              Input("timer_data", "value"),
              State("timer_memory", "data"),
              State("musik_start", "value"),
              Input("start_button", "n_clicks"),
              Input("reset_button", "n_clicks"),
              Input("interval", "n_intervals"))
def update_timer(data, max_time, current_timer, music_start,
                 _start_clicks, _reset_clicks, n_intervals):
    max_time = max_time or 0
    timer_interval = current_timer
    trigger = dash.callback_context.triggered[0]["prop_id"]
    music_event = None

    if trigger == "reset_button.n_clicks":
        # Explicit reset: stop and rewind to the set duration, even
        # mid-run, so a changed value applies right away.
        timer_interval = max_time
        data["running"] = False
        music_event = "reset"
    elif trigger == "timer_data.value":
        # Editing the duration while idle previews it straight away; a
        # running match keeps counting until Reset is pressed.
        if not data["running"]:
            timer_interval = max_time
    elif trigger == "start_button.n_clicks":
        # Toggle off the actual running state, not the button text
        # (which now carries an icon and is not a bare string).
        if not data["running"]:
            if timer_interval <= 0:
                timer_interval = max_time
            data["running"] = True
            music_event = "start_match"
        else:
            # Stop ends the attempt and rewinds to the set duration so
            # the next Start is ready and it is visible right away.
            data["running"] = False
            timer_interval = max_time
            music_event = "reset"
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

    label = f"{format_clock(timer_interval)} ({timer_interval} s)"
    return (data, _start_button_label(data["running"]), timer_interval,
            label, music_command)


@app.callback(
    Output("timer_progress", "value"),
    State("data_memory", "data"),
    Input("timer_memory", "data"))
def update_progressbar(data, current_timer):
    return progress_percent(current_timer, data.get("max_time", 0))


if __name__ == '__main__':
    app.run(debug=True)