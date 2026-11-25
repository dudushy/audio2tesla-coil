# coil-prep

[🇧🇷 Português](README.md) · 🇬🇧 English

Web app (runs in Docker) that turns a song (YouTube link or file) into its **melody as a square wave**, ready to play on a **plasma speaker** (musical mini Tesla coil) connected by a 3.5 mm cable to a laptop.

The coil plays **one note at a time** as a low-fidelity square wave, so the app does not "clean" the mix: it extracts the **melody** only and renders it as a square wave inside the frequency range the coil plays well.

```
link or file → (excerpt start/end) → (Demucs: isolate a stem) → notes (Basic Pitch or .mid) → 1 voice at a time
→ audio: rhythm snapped to the song's tempo grid → whole song shifted by octaves to the middle of the range
→ octave chosen by contour inside the coil range → 50% square wave (peak -8 dBFS) → WAV + MIDI
```

## Usage

Requires [Docker](https://docs.docker.com/engine/install/) with Compose.

```bash
git clone https://github.com/dudushy/audio2tesla-coil && cd audio2tesla-coil
docker compose up --build
```

Or run `./start.sh` (Linux, macOS, WSL): it starts the app in the background and **prints the address to copy** to other devices on the network. `./start.sh --ip` only prints the addresses.

Open **http://localhost:1337** (or `http://<laptop-IP>:1337` from another device on the LAN) in the browser of the laptop connected to the coil, paste a link or upload a file (audio or `.mid`, up to 100 MB), pick the stem (`other` default, `vocals`, or `mix`), hit **Generate**, listen to the result and the original, and download the WAV. Audio plays **in the browser**, so it comes out of the laptop's headphone jack, the same one wired to the coil. **Start with the volume LOW.**

- First run downloads the Demucs weights; separation runs on **CPU** (a few minutes per song) and is cached.
- Data lives in `./data` (git-ignored); model weights live in a Docker volume.
- Without calibration the app uses 100-800 Hz. The **Calibrar** page plays each note from C1 to B8 for 2 s (with a 3 s rest between notes) and asks whether you heard it on the coil (volume LOW); generated melodies are then folded by octaves onto the heard notes. Stored in `data/device.json`.
- The **Partituras** page builds melodies straight from note files. For games, memes and hits it searches BitMidi and takes the `.mid` you downloaded (personal use only). For classics: a list of famous pieces plus search on the [Mutopia Project](https://www.mutopiaproject.org/) (public domain / Creative Commons), using the score's `.mid`.
- Rate each result (👍/👎 + reason) after trying it on the coil; ratings go to `data/feedback.jsonl` and the app suggests the stem with the best average.
- Port **1337** is published on `0.0.0.0` (LAN). The API has no authentication and downloads videos on request: trusted networks only; use `COIL_BIND=127.0.0.1` for local-only. On Windows with WSL in `mirrored` networking mode, the WSL firewall blocks other devices: run `./start.sh --liberar-firewall` once (asks for Windows administrator rights; opens TCP 1337 only; undo with `Remove-NetFirewallHyperVRule -Name 'audio2tesla-coil-1337'`). The new **Soundpad** page (`/soundpad`) is a full-screen grid of all notes (one pad per note, one colour per octave) with multi-touch chords, record/playback, WAV/MP3/MIDI export and a full-screen mode (pads and buttons only). With "fit to coil range" checked only the calibrated notes are shown.
- Download only content you have the right to use; YouTube's terms restrict downloads outside official means. Never commit audio.

⚠️ High voltage and plasma arc: follow the manufacturer's instructions and keep the volume low.

## Development

```bash
uv venv --python 3.11 && source .venv/bin/activate
uv pip install -e ".[dev]"      # + ".[midi,separate]" for audio input and Demucs
ruff check . && pytest
python -m coil_prep.evaluate expected.mp3 generated.wav   # playability gate + note F1 vs an expected square-wave render
COIL_DATA=./data uvicorn coil_prep.web.app:create_default_app --factory --reload
```

Credits: [Demucs](https://github.com/adefossez/demucs) (MIT), [Basic Pitch](https://github.com/spotify/basic-pitch) (Apache-2.0), [yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense). License: [MIT](LICENSE).
