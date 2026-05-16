let spotifyToken = null;
let player = null;

// SDK Entry Point
window.onSpotifyWebPlaybackSDKReady = () => {
    console.log("Spotify SDK ready");
    if (spotifyToken) {
        initializePlayer(spotifyToken);
    }
};

// Initialize player
function initializePlayer(token) {
    player = new Spotify.Player({
        name: "Dash Spotify Player",
        getOAuthToken: cb => cb(token),
        volume: 0.5,
    });

    player.addListener("ready", ({ device_id }) => {
        console.log("Player ready with device ID:", device_id);
        window.postMessage({ type: "spotify-player-ready", device_id }, window.location.origin);
    });

    player.addListener("not_ready", ({ device_id }) => {
        console.log("Device ID has gone offline", device_id);
        window._spotify_device_id = null;
    });

    bindPlayerStateListener();

    player.connect().then(success => {
    if (success) {
        console.log("Player connected!");
    }
});
}

// Wait until DOM has loaded and button is present
const waitForButton = setInterval(() => {
    const btn = document.getElementById("connect-button");
    if (btn && !btn.dataset.listenerAttached) {
        console.log("Attaching listener to Spotify Connect button");

        btn.addEventListener("click", () => {
            console.log("Opening Spotify login popup...");
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
                    console.log("Received token:", spotifyToken);

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
        console.log("Dash notified: authenticated");
        window._spotify_status = "authenticated";
    }

    if (event.data.type === "spotify-player-ready") {
        console.log("Dash notified: player ready", event.data.device_id);
        window._spotify_status = "player-ready";
        window._spotify_device_id = event.data.device_id;
    }
});

// function waitForPlayerButtonsAndBind(player) {
//     const intervalId = setInterval(() => {
//         const playBtn = document.getElementById("play-btn");
//         const pauseBtn = document.getElementById("pause-btn");
//         const nextBtn = document.getElementById("next-btn");
//         const prevBtn = document.getElementById("prev-btn");
//
//         if (playBtn && pauseBtn && nextBtn && prevBtn) {
//             clearInterval(intervalId);
//
//             playBtn.onclick = () => player.resume();
//             pauseBtn.onclick = () => player.pause();
//             nextBtn.onclick = () => player.nextTrack();
//             prevBtn.onclick = () => player.previousTrack();
//
//             console.log("Player controls bound");
//         }
//     }, 200); // check every 200ms until buttons exist
// }


// Render the currently playing track into the track-info panel.
function renderTrackInfo(track) {
    const div = document.getElementById("track-info");
    if (!div) return;
    if (!track) {
        div.innerHTML = "";
        return;
    }
    const artists = track.artists.map(a => a.name).join(", ");
    const cover = track.album.images.length ? track.album.images[0].url : "";
    div.innerHTML = `<strong>${track.name}</strong> by ${artists}<br/>` +
        (cover ? `<img src="${cover}" width="100"/>` : "");
}

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

// Single source of player updates: the SDK pushes a state on every
// meaningful change, so no polling loop is needed.
function bindPlayerStateListener() {
    player.addListener("player_state_changed", state => {
        const playstate = deriveSpotifyPlaystate(state);
        window._spotify_playstate = playstate;
        renderTrackInfo(state && state.track_window
            ? state.track_window.current_track : null);
    });
}