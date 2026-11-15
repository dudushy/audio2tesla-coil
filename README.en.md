# coil-prep

[🇧🇷 Português](README.md) · 🇬🇧 English

Python CLI that prepares audio tracks to play on a **plasma speaker** (musical mini Tesla coil, "square wave music" mode) connected through a 3.5 mm (TRS) cable to a laptop.

## What it is and isn't

A plasma speaker plays a low-fidelity square wave, like a tweeter. So "cleaning" the audio here is **not denoising**: it means simplifying (mono, no extreme lows/highs) and **avoiding clipping**, since too loud a signal distorts.

**Limitations:** not hi-fi. Dense mixes sound muddy; simple melodies, one note at a time, sound best. Preset values are starting points: tune them by ear.

## Status

| Feature | Status |
|---|---|
| `prep`: mono, high/low-pass and limiter (ffmpeg), presets, batch | ✅ |
| `check`: peak, RMS and clipping | ✅ |
| `calibrate`: test tones + coil profile (min/max Hz range) | ✅ |
| `midi`: melody from `.mid` or audio → monophonic square wave, within the profile | ✅ (experimental) |
| `--separate`: isolates a stem with Demucs before `prep`/`midi` | ✅ (experimental) |

## Install

Requires Python 3.10+ and [ffmpeg](https://ffmpeg.org/) on `PATH`. For `midi` with audio use Python 3.10 or 3.11 (see below).

**Linux** (bash/zsh) or **WSL / Ubuntu**
```bash
sudo apt install ffmpeg python3-venv
git clone https://github.com/dudushy/audio2tesla-coil
cd audio2tesla-coil
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

**Linux with Python 3.11 via [uv](https://docs.astral.sh/uv/) (recommended for `midi` with audio and `--separate`)**

Basic Pitch (the `[midi]` extra) does not install on Python 3.12+ (e.g. the 3.14 some distros ship). `uv` downloads 3.11 for you without touching the system Python:
```bash
sudo apt install ffmpeg
curl -LsSf https://astral.sh/uv/install.sh | sh        # installs uv (or: pipx install uv)
git clone https://github.com/dudushy/audio2tesla-coil
cd audio2tesla-coil
uv venv --python 3.11 ~/.venvs/coil-prep
source ~/.venvs/coil-prep/bin/activate
uv pip install -e ".[midi,separate]"                   # Basic Pitch + Demucs (several GB)
coil-prep --version
```
The `[midi]` extra already pins `numpy<2` and `setuptools<81` (required by TensorFlow and resampy); do not install newer versions on top. Without the extras (plain `pip install -e .`), `prep`, `check`, `calibrate` and `midi` with `.mid` work.

**PowerShell (Windows)**
```powershell
winget install Gyan.FFmpeg        # open a new terminal afterwards
git clone https://github.com/dudushy/audio2tesla-coil
cd audio2tesla-coil
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

Tip: audio leaves through the headphone jack of the system where you play the WAV; play it where you generated it, or copy the file.

## Full workflow (song → coil)

Work in a dedicated folder: `device.json` and `separated/` (stem cache) are created **in the current folder**.
```bash
mkdir -p ~/WorkspaceLinux/tesla-coil/{input,output} && cd ~/WorkspaceLinux/tesla-coil
source ~/.venvs/coil-prep/bin/activate

# 1) once: find the coil's range by ear (LOW volume)
coil-prep calibrate tones -o output/calibrate.wav     # plays 100..2000 Hz, one tone every 2 s
coil-prep calibrate save --min 100 --max 1000          # the Hz that sounded like a clear note

# 2) per song: monophonic melody ("other" stem is the --separate default)
coil-prep midi input/song.mp3 -o output/song.wav --profile device.json --separate
coil-prep check output/song.wav                        # peak and clipping

# lighter alternative: busy songs -> filters only (no melody)
coil-prep prep input/song.mp3 -o output/song_prep.wav --profile device.json
```
Only `prep` takes a folder (batch); `midi` processes one file at a time. The first `--separate` downloads the Demucs model and takes a few minutes per song on CPU; afterwards the stem is cached in `separated/` and re-runs are fast. Always start with the coil's **volume LOW**.

## Usage

```bash
coil-prep prep input.mp3 -o output.wav             # default preset
coil-prep prep input.mp3 -o output.wav --preset vocal
coil-prep prep input.mp3 -o output.wav --highpass 200 --lowpass 5000 --limit 0.6
coil-prep prep my_music/ -o out/                   # batch (mp3, wav, flac, ogg, m4a)
coil-prep check output.wav                         # exit code 1 if peak > -3 dBFS or clipping (--max-peak changes the limit)
```

| Preset | High-pass | Low-pass | Limit |
|---|---|---|---|
| `default` | 150 Hz | 8000 Hz | 0.7 (~ -3 dBFS) |
| `vocal` | 200 Hz | 4000 Hz | 0.7 |
| `safe` | 150 Hz | 6000 Hz | 0.5 (~ -6 dBFS) |

`prep` always measures the result and warns if the peak exceeds -3 dBFS. Output: 16-bit mono WAV, 44.1 kHz (`--rate` changes it). `check` decodes through ffmpeg, so it reads any format.

## Melody mode (`midi`): the main path

Filtering a full mix (`prep`) keeps drums, bass and synths in range, and the coil plays **one note at a time**, so it turns into noise. `midi` extracts only the **melody**, like someone humming:

```
.mid or audio → notes → one voice per instant (amplitude + pitch + continuity) → fragment cleanup → octave chosen by contour inside the coil range → square wave
```
```bash
coil-prep midi song.mid -o out.wav --profile device.json   # .mid: light, numpy + mido only
pip install -e ".[midi]"                                    # extra for audio (Basic Pitch)
coil-prep midi song.mp3 -o out.wav --profile device.json
```
Options: `--voices N` (default 1), `--min-note S` (drops notes shorter than S seconds, default 0.06), `--gap S` (silence between touching or repeated notes, default 0.02), `--min-hz/--max-hz` (override the profile; without a profile 100–800 Hz is used), `--peak-db` (default -8).

Note cleanup (1 voice): `--select weighted|skyline` (default `weighted`: scores amplitude, pitch and closeness to the previous note, which ignores weak high noise and sustained bass; `skyline` = always the highest note, the old behaviour), `--merge-gap S` (joins fragments of the same note separated by less than S s, default 0.02; 0 disables; touching repeated notes are not merged) and `--detect-min-note MS` (shortest note Basic Pitch detects, default 80 ms; its own default is 127.7). Each note's octave follows the contour (closest to the previous note inside the range), avoiding false octave jumps.

- A 3-octave range (like 100–800 Hz) always fits any note by octave folding, keeping the melody; ranges under 1 octave are an error.
- **Limitation:** transcription of a full mix is error-prone, and "highest note" is not always the melody. A melody `.mid` usually sounds better; use `--separate` to isolate a stem first (below). `midi` warns when more than 30% of the time has overlapping notes, a sign the melody may come out wrong.
- **`[midi]` extra (Basic Pitch):** on Linux it needs **Python 3.10 or 3.11** (`tensorflow<2.15.1` and `tflite-runtime` have no wheels for 3.12+). The `.mid` mode works on any Python 3.10+.

### Isolate a stem first (`--separate`)

```bash
pip install -e ".[separate]"        # Demucs + PyTorch (several GB); the model downloads on first run
coil-prep midi song.mp3 -o out.wav --profile device.json --separate                  # "other" stem (default)
coil-prep midi song.mp3 -o out.wav --profile device.json --separate --stem vocals
```
Runs on **CPU**, so it takes a few minutes per song. The stem is cached in `separated/` (git-ignored): rerunning on the same song is fast. `--stem` accepts `other` (default: synths and instruments, where the melody usually is in electronic music), `vocals` (better for sung music), `bass` and `drums`. Also works with `prep`. For the `[midi]` and `[separate]` extras together, use Python 3.10 or 3.11. Tip to save disk: install first `pip install torch --index-url https://download.pytorch.org/whl/cpu`.

## Coil calibration

There's no point generating frequencies the coil can't play. Find the range by ear:
```bash
coil-prep calibrate tones -o calibrate.wav     # square tones, low volume (-8 dBFS)
# tones run from 100 to 2000 Hz (--freqs changes the grid); play calibrate.wav on the coil (start with LOW volume) and note which tones sound like a clear note
coil-prep calibrate save --min 100 --max 1000  # writes device.json in the current folder (git-ignored)
coil-prep prep input.mp3 -o output.wav --profile device.json   # filters = profile min/max
```
Cutoff priority: `--highpass/--lowpass` > `--profile` > `--preset`.

## ⚠️ Safety

**Start with the laptop volume LOW** and raise it slowly with the coil on. High voltage and plasma arc: follow the manufacturer's instructions and keep away from children, electronics and flammable materials.

## Development

```bash
pip install -e ".[dev]"
ruff check . && pytest
```
Tests generate synthetic audio; **never commit copyrighted music or audio** (`.gitignore` blocks mp3/wav/flac/mid/ogg).

## Credits

- [Demucs](https://github.com/adefossez/demucs) (MIT), stem separation (`--separate`)
- [Basic Pitch](https://github.com/spotify/basic-pitch) (Apache-2.0, Spotify), audio → MIDI (`[midi]` extra)
- [midi2tesla](https://github.com/yspacedev/midi2tesla): idea reference only, no code reused

## License

[MIT](LICENSE)
