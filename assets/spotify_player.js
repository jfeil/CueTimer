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


function updatePlayer() {
    player.getCurrentState().then(state => {
        if (!state || !state.track_window || !state.track_window.current_track) return;

        const track = state.track_window.current_track;
        const trackInfoDiv = document.getElementById("track-info");
        if (trackInfoDiv) {
            trackInfoDiv.innerHTML = `
                <strong>${track.name}</strong> by ${track.artists.map(a => a.name).join(", ")}<br/>
                <img src="${track.album.images[0].url}" width="100"/>
            `;
        }
    });
}

setInterval(() => {
    if (!player) return;
    updatePlayer();
}, 50);

let _playback_command = null;
let lastCommandTimestamp = -1;

setInterval(() => {
    console.log(_playback_command);
    if (!_playback_command) return;

    // Dash renders dcc.Store content in a hidden div with JSON string inside
    // So we parse its children text content
    if (player) {
        if (_playback_command.ts > lastCommandTimestamp) {
            if (_playback_command) {
                if (_playback_command.command === "play") {
                    player.resume();
                } else if (_playback_command.command === "pause") {
                    player.pause();
                } else if (_playback_command.command === "prev") {
                    player.previousTrack();
                } else if (_playback_command.command === "next") {
                    player.nextTrack();
                }
            }
        }
    }
}, 50); // poll every 1s