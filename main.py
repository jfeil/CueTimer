import time

import dash
from dash import Input, Output, dcc, html, State
import dash_bootstrap_components as dbc
import spotipy
from spotipy import SpotifyOAuth
from flask import Flask, request, redirect, session
import os
import uuid

from dotenv import load_dotenv

load_dotenv()

# --- Configuration ---
CLIENT_ID = os.environ["SPOTIFY_CLIENT_ID"]
CLIENT_SECRET = os.environ["SPOTIFY_CLIENT_SECRET"]
REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8050/callback")
SCOPE = "streaming,user-read-email,user-read-private,user-library-read"

server = Flask(__name__)
server.secret_key = os.urandom(24)

# Spotipy OAuth
oauth = SpotifyOAuth(client_id=CLIENT_ID,
                     client_secret=CLIENT_SECRET,
                     redirect_uri=REDIRECT_URI,
                     scope=SCOPE)


def get_spotify():
    """Return an authed spotipy client, or None if not yet authenticated.

    Reads the token from spotipy's cache and refreshes it transparently;
    a single operator drives one player, so the shared cache is sufficient.
    """
    token_info = oauth.cache_handler.get_cached_token()
    if not token_info:
        return None
    if oauth.is_token_expired(token_info):
        token_info = oauth.refresh_access_token(token_info["refresh_token"])
    return spotipy.Spotify(auth=token_info["access_token"])


def track_to_item(track):
    """Normalize a Spotify track object into a queue entry.

    rowId is a stable per-entry id so the same track can appear multiple
    times and drag-reorder can track rows independently of track identity.
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

dbc_css = "https://cdn.jsdelivr.net/gh/AnnMarieW/dash-bootstrap-templates/dbc.min.css"
app = dash.Dash(__name__, server=server,
                external_scripts=["https://sdk.scdn.co/spotify-player.js"],
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
    dcc.Store(id="data_memory", storage_type="memory", data={"running": False, "max_time": 0}),
    dcc.Store(id="timer_memory", storage_type="memory", data=default_time),
    dcc.Store(id="playback-command", data=None),
    dcc.Store(id="device-id", storage_type="memory"),
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
    ]),
    dbc.Label(id="Musik")]),
    html.Br(),
    dbc.Container([
    html.Div(id="main", children=[
        dbc.Button("Connect to Spotify", id="connect-button"),
        html.Div(id="player-status"),
    ]),
    html.Div(id="track-info"),
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
    function(n, status, playback_data) {
        const ts = Date.now();
        window._playback_command = playback_data;
        console.log("Updated playback command:", window._playback_command);
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
    State("playback-command", "data")
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
                html.Span(f"{idx + 1}.", className="text-muted me-2"),
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
        ], id={"type": "queue-row", "row": t["rowId"]}))
    return items


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

@app.callback(
    Output("spotify-ts", "data"),
    Output("main", "children"),
    State("spotify-ts", "data"),
    Input("spotify-status", "data")
)
def update_main_layout(ts, status):
    if ts and ts >= status["ts"]:
        return ts, dash.no_update
    if status["state"] == "authenticated":
        return status["ts"], html.Div([
            html.H4("✅ Authenticated with Spotify"),
            html.Div(id="player-status", children="Waiting for player to be ready..."),
        ])
    elif status["state"] == "player-ready":
        return status["ts"], html.Div([
            html.H4("🎵 Spotify Web Player Connected"),
            html.Div(id="player-status", children="Player is ready"),
            html.Div(id="track-info", children="No track playing yet."),
            html.Div([
                dbc.Button("⏮️", id="prev-btn"),
                dbc.Button("▶️", id="play-btn"),
                dbc.Button("⏸️", id="pause-btn"),
                dbc.Button("⏭️", id="next-btn"),
            ], style={"marginTop": "1rem"})
        ])
    else:
        return status["ts"], html.Div([
            dbc.Button("Connect to Spotify", id="connect-button"),
            html.Div(id="player-status")
        ])

@app.callback(
    Output("playback-command", "data"),
    Input("play-btn", "n_clicks"),
    Input("next-btn", "n_clicks"),
    Input("prev-btn", "n_clicks"),
    Input("pause-btn", "n_clicks"),
    prevent_initial_call=True
)
def trigger_play(*_):
    current_time = time.time()
    command = None
    if dash.callback_context.triggered[0]["prop_id"] == "play-btn.n_clicks":
        # send play command with a timestamp (to trigger each time)
        command = "play"
    elif dash.callback_context.triggered[0]["prop_id"] == "next-btn.n_clicks":
        # send play command with a timestamp (to trigger each time)
        command = "next"
    elif dash.callback_context.triggered[0]["prop_id"] == "prev-btn.n_clicks":
        # send play command with a timestamp (to trigger each time)
        command = "prev"
    elif dash.callback_context.triggered[0]["prop_id"] == "pause-btn.n_clicks":
        # send play command with a timestamp (to trigger each time)
        command = "pause"
    if command:
        print(command)
        return {"command": command, "ts": current_time}
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
              State("data_memory", "data"),
              State("timer_data", "value"),
              State("timer_memory", "data"),
              State("start_button", "children"),
              Input("start_button", "n_clicks"),
              Input("reset_button", "n_clicks"),
              Input("interval", "n_intervals"))
def update_timer(data, max_time, current_timer, button_label, *_):
    timer_interval = current_timer
    label_button = button_label
    if dash.callback_context.triggered[0]["prop_id"] == "reset_button.n_clicks":
        timer_interval = max_time
    elif dash.callback_context.triggered[0]["prop_id"] == "start_button.n_clicks":
        if button_label == "Start":
            if timer_interval <= 0:
                timer_interval = max_time
            data["running"] = True
            label_button = "Stop"
        else:
            data["running"] = False
            label_button = "Start"
    elif dash.callback_context.triggered[0]["prop_id"] == "interval.n_intervals" and data["running"]:
        timer_interval -= 1
    data["max_time"] = max_time

    return data, label_button, timer_interval, timer_interval


@app.callback(
    Output("timer_progress", "value"),
    State("data_memory", "data"),
    Input("timer_memory", "data"))
def update_progressbar(data, current_timer):
    return current_timer / data["max_time"] * 100

@app.callback(
    Output("Musik", "children"),
    State("musik_start", "value"),
    Input("timer_memory", "data"))
def update_musik(musik_start, current_timer):
    if musik_start >= current_timer:
        return "MUSIK LÄUFT! :)"
    else:
        return ""


if __name__ == '__main__':
    app.run(debug=True)