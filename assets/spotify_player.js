let spotifyToken = null;
let player = null;

// SDK Entry Point
window.onSpotifyWebPlaybackSDKReady = () => {
    if (spotifyToken) {
        initializePlayer(spotifyToken);
    }
};

// Initialize player
function initializePlayer(token) {
    player = new Spotify.Player({
        name: "Cue Timer",
        getOAuthToken: cb => cb(token),
        volume: 0.5,
    });

    player.addListener("ready", ({ device_id }) => {
        window.postMessage({ type: "spotify-player-ready", device_id }, window.location.origin);
    });

    player.addListener("not_ready", () => {
        window._spotify_device_id = null;
    });

    bindPlayerStateListener();
    player.connect();
}

// Wait until DOM has loaded and button is present
const waitForButton = setInterval(() => {
    const btn = document.getElementById("connect-button");
    if (btn && !btn.dataset.listenerAttached) {
        btn.addEventListener("click", () => {
            const authWindow = window.open(
                "/login",
                "Spotify Login",
                "width=600,height=800"
            );

            if (!authWindow) {
                alert("Please allow popups for this site.");
                return;
            }

            // Receive token from popup via postMessage
            window.addEventListener("message", event => {
                if (event.origin !== window.location.origin) return;
                if (event.data.token) {
                    spotifyToken = event.data.token;

                    // Notify Dash we are authenticated
                    window.postMessage({ type: "spotify-authenticated" }, window.location.origin);

                    if (window.Spotify) {
                        initializePlayer(spotifyToken);
                    }
                }
            }, { once: true });
        });

        btn.dataset.listenerAttached = "true";
        clearInterval(waitForButton);
    }
}, 300);

window._spotify_status = null;
window._spotify_device_id = null;
window.addEventListener("message", (event) => {
    if (event.origin !== window.location.origin) return;

    if (event.data.type === "spotify-authenticated") {
        window._spotify_status = "authenticated";
    }

    if (event.data.type === "spotify-player-ready") {
        window._spotify_status = "player-ready";
        window._spotify_device_id = event.data.device_id;
    }
});

// Translate the SDK state into the flat shape the server consumes,
// inferring track-end (paused at position 0 on the track we were playing)
// since the SDK has no explicit "ended" event.
let _lastTrackUri = null;

function deriveSpotifyPlaystate(state) {
    if (!state || !state.track_window || !state.track_window.current_track) {
        return null;
    }
    const track = state.track_window.current_track;
    const ended = state.paused && state.position === 0 &&
        _lastTrackUri === track.uri;
    _lastTrackUri = track.uri;
    return {
        uri: track.uri,
        paused: state.paused,
        position: state.position,
        duration: state.duration,
        ended: ended,
        ts: Date.now(),
    };
}

// Keep the playstate fresh from two sources: the event (snappy, fires
// on play/pause/seek/track change) and a 1s poll of getCurrentState
// (authoritative position). The poll matters because playback is
// driven server-side — when we start a track at an offset or switch
// songs while one is running, the event can be stale or skipped, so
// without it the bar would keep the previous song's position.
function bindPlayerStateListener() {
    player.addListener("player_state_changed", state => {
        window._spotify_playstate = deriveSpotifyPlaystate(state);
    });
    setInterval(() => {
        player.getCurrentState().then(state => {
            const s = deriveSpotifyPlaystate(state);
            if (s) window._spotify_playstate = s;
        });
    }, 1000);
}

// Read the queue's DOM order back into the rowId list the server
// understands. Dash serializes pattern-matching ids as JSON with keys
// sorted alphabetically, so each row's element id parses to {row,type}.
function readQueueOrder(list) {
    return Array.from(list.children).map(el => {
        try { return JSON.parse(el.id).row; } catch (e) { return null; }
    }).filter(Boolean);
}

// (Re)attach SortableJS to the queue list. Called after every queue
// re-render, so any stale instance is torn down first. Touch works
// because dragging is restricted to the .drag-handle, leaving the rest
// of the row free to scroll on a phone/tablet.
window.initQueueSortable = function () {
    const list = document.getElementById("spotify-tracks");
    if (!list || !window.Sortable) return;
    if (list._sortable) list._sortable.destroy();
    list._sortable = window.Sortable.create(list, {
        animation: 150,
        handle: ".drag-handle",
        onEnd: function () {
            window.dash_clientside.set_props("queue-order", {
                data: { ids: readQueueOrder(list), ts: Date.now() },
            });
        },
    });
};