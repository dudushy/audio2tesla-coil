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
| `--separate`: Demucs as a pre-step | planned |

## Install

Requires Python 3.10+ and [ffmpeg](https://ffmpeg.org/) on `PATH`.

**Linux** (bash/zsh) or **WSL / Ubuntu**
```bash
sudo apt install ffmpeg python3-venv
git clone https://github.com/dudushy/audio2tesla-coil
cd audio2tesla-coil
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

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

## Usage

```bash
coil-prep prep input.mp3 -o output.wav             # default preset
coil-prep prep input.mp3 -o output.wav --preset vocal
coil-prep prep input.mp3 -o output.wav --highpass 200 --lowpass 5000 --limit 0.6
coil-prep prep my_music/ -o out/                   # batch (mp3, wav, flac, ogg, m4a)
coil-prep check output.wav                         # exit code 1 if peak > -3 dBFS or clipping
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
.mid or audio → notes → highest note at each instant ("skyline") → octave-folded into the coil range → square wave
```
```bash
coil-prep midi song.mid -o out.wav --profile device.json   # .mid: light, numpy + mido only
pip install -e ".[midi]"                                    # extra for audio (Basic Pitch)
coil-prep midi song.mp3 -o out.wav --profile device.json
```
Options: `--voices N` (default 1), `--min-note S` (drops notes shorter than S seconds, default 0.06), `--min-hz/--max-hz` (override the profile; without a profile 100–800 Hz is used), `--peak-db` (default -4).

- A 3-octave range (like 100–800 Hz) always fits any note by octave folding, keeping the melody; ranges under 1 octave are an error.
- **Limitation:** transcription of a full mix is error-prone, and "highest note" is not always the melody. A melody `.mid` usually sounds better; isolating vocals with Demucs is planned.
- **`[midi]` extra (Basic Pitch):** on Linux it needs **Python 3.10 or 3.11** (`tensorflow<2.15.1` and `tflite-runtime` have no wheels for 3.12+). The `.mid` mode works on any Python 3.10+.

## Coil calibration

There's no point generating frequencies the coil can't play. Find the range by ear:
```bash
coil-prep calibrate tones -o calibrate.wav     # square tones, low volume (-12 dBFS)
# play calibrate.wav on the coil (start with LOW volume) and note which tones sound like a clear note
coil-prep calibrate save --min 200 --max 3000  # writes device.json (git-ignored)
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

- [Demucs](https://github.com/adefossez/demucs) (MIT), stem separation (planned)
- [Basic Pitch](https://github.com/spotify/basic-pitch) (Apache-2.0, Spotify), audio → MIDI (planned)
- [midi2tesla](https://github.com/yspacedev/midi2tesla): idea reference only, no code reused

## License

[MIT](LICENSE)
