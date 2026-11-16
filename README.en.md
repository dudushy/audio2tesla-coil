# coil-prep

[🇧🇷 Português](README.md) · 🇬🇧 English

Web app (runs in Docker) that turns a song (YouTube link or file) into its **melody as a square wave**, ready to play on a **plasma speaker** (musical mini Tesla coil) connected by a 3.5 mm cable to a laptop.

The coil plays **one note at a time** as a low-fidelity square wave, so the app does not "clean" the mix: it extracts the **melody** only and renders it as a square wave inside the frequency range the coil plays well.

```
link or file → (Demucs: isolate a stem) → notes (Basic Pitch or .mid) → 1 voice at a time
→ octave chosen by contour inside the coil range → square wave (peak -8 dBFS) → WAV
```

## Usage

Requires [Docker](https://docs.docker.com/engine/install/) with Compose.

```bash
git clone https://github.com/dudushy/audio2tesla-coil && cd audio2tesla-coil
docker compose up --build
```

Open **http://localhost:8000** in the browser of the laptop connected to the coil, paste a link or upload a file (audio or `.mid`, up to 100 MB), pick the stem (`other` default, `vocals`, or `mix`), hit **Generate**, listen to the result and the original, and download the WAV. Audio plays **in the browser**, so it comes out of the laptop's headphone jack, the same one wired to the coil. **Start with the volume LOW.**

- First run downloads the Demucs weights; separation runs on **CPU** (a few minutes per song) and is cached.
- Data lives in `./data` (git-ignored); model weights live in a Docker volume.
- Without calibration the app uses 100-800 Hz. Use **Calibrar a bobina** on the page: play the test tones (volume LOW), note which sound clear, save min/max Hz (at least 1 octave). Stored in `data/device.json`.
- Rate each result (👍/👎 + reason) after trying it on the coil; ratings go to `data/feedback.jsonl` and the app suggests the stem with the best average.
- The port is bound to `127.0.0.1` only: the API has no authentication and downloads videos on request.
- Download only content you have the right to use; YouTube's terms restrict downloads outside official means. Never commit audio.

⚠️ High voltage and plasma arc: follow the manufacturer's instructions and keep the volume low.

## Development

```bash
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -e ".[dev]"      # + ".[midi,separate]" for audio input and Demucs
ruff check . && pytest
COIL_DATA=./data uvicorn coil_prep.web.app:create_default_app --factory --reload
```

Credits: [Demucs](https://github.com/adefossez/demucs) (MIT), [Basic Pitch](https://github.com/spotify/basic-pitch) (Apache-2.0), [yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense). License: [MIT](LICENSE).
