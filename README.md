# Cue Timer

A countdown timer that automatically cues music. Set a match/interval
duration and a "Musik ab" point; when the countdown reaches it the app
plays from a queue (search or whole/partial Spotify playlists), keeps
playing through the break, and stops when the next interval starts.
Playback is the Spotify Web Playback SDK driven server-side via spotipy.

## Requirements

- A **Spotify Premium** account (the Web Playback SDK needs Premium).
- A Spotify app in the [developer dashboard](https://developer.spotify.com/dashboard)
  for the Client ID/Secret and the redirect URI.
- The page must be served over **HTTPS** (or `localhost`) — the Web
  Playback SDK refuses to run otherwise. In production put the app
  behind a TLS-terminating reverse proxy.

## Configuration

Copy `.env.example` to `.env` and fill in:

| Variable | Purpose |
|---|---|
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | From the Spotify dashboard. |
| `SPOTIFY_REDIRECT_URI` | `<public-url>/callback`, registered **verbatim** in the dashboard. |
| `SPOTIFY_CACHE_PATH` | Where the OAuth token is stored (default `.cache`). |
| `SECRET_KEY` | Flask session key; set a stable random value in production. |

## Run locally

```bash
uv sync
uv run python main.py        # http://127.0.0.1:8050
```

Run the tests with `uv run pytest -q`.

## Run with Docker

```bash
docker build -t cue-timer .
docker run --rm -p 8050:8050 --env-file .env \
  -e SPOTIFY_REDIRECT_URI=http://127.0.0.1:8050/callback \
  -v cue-timer-cache:/data cue-timer
```

The image runs gunicorn (single worker, threaded) and stores the token
under `/data` — keep that on a volume so a redeploy stays signed in.

## Deploy with Compose

`docker-compose.yml` is a template. Edit the `environment` block
(`SPOTIFY_REDIRECT_URI`, `SECRET_KEY`), ensure `.env` exists, then:

```bash
docker compose up -d
```

By default it builds locally; switch the `image:` line to the
GHCR image to deploy a published build instead. Front it with a
reverse proxy that terminates TLS and forwards to port `8050`, and
make sure the proxy's public URL + `/callback` is the registered
redirect URI.

## Continuous build

`.github/workflows/ci.yml` runs the pytest suite on every push/PR and,
on `main` and `v*` tags, builds the Docker image and pushes it to
`ghcr.io/<owner>/<repo>` (`latest`, the tag, and the commit SHA).
Pull requests build the image as a check but do not push.

## Notes

- Single operator by design: one shared, file-backed token cache.
- Spotify-owned algorithmic/editorial playlists (Daily Mix, Radio,
  Editorial — the `37i9dQZF1…` ids) are not accessible via the Web API
  and will report a clear error; use your own playlists.
