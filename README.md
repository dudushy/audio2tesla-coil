# coil-prep

🇧🇷 Português · [🇬🇧 English](README.en.md)

CLI em Python que prepara faixas de áudio para tocar em um **plasma speaker** (mini bobina de Tesla musical, modo "square wave music") ligado por cabo P2 (3,5 mm) a um notebook.

## O que é e o que não é

O plasma speaker reproduz uma onda quadrada de baixa fidelidade, como um tweeter. Por isso "limpar" o áudio aqui **não é denoise**: é simplificar (mono, sem graves nem agudos extremos) e **evitar clipping**, porque sinal alto demais distorce.

**Limitações:** não é hi-fi. Música com muitos instrumentos soa embolada; melodias simples e uma nota por vez soam melhor. Os valores dos presets são pontos de partida e devem ser validados de ouvido.

## Status

| Recurso | Status |
|---|---|
| `prep`: mono, passa-alta/baixa e limiter (ffmpeg), presets, lote | ✅ |
| `check`: pico, RMS e clipping | ✅ |
| `midi`: MIDI/áudio → onda quadrada (1 voz por padrão) | planejado |
| `--separate`: Demucs como pré-etapa | planejado |

## Instalação

Requisitos: Python 3.10+ e [ffmpeg](https://ffmpeg.org/) no `PATH`.

**PowerShell (Windows)**
```powershell
winget install Gyan.FFmpeg        # abra um novo terminal depois
git clone https://github.com/dudushy/audio2tesla-coil
cd audio2tesla-coil
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

**WSL / Ubuntu**
```bash
sudo apt install ffmpeg python3-venv
git clone https://github.com/dudushy/audio2tesla-coil
cd audio2tesla-coil
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```
Dica: o áudio sai pelo P2 do Windows, então gerar e tocar o WAV no próprio Windows evita copiar arquivos entre os sistemas.

## Uso

```bash
coil-prep prep entrada.mp3 -o saida.wav            # preset default
coil-prep prep entrada.mp3 -o saida.wav --preset vocal
coil-prep prep entrada.mp3 -o saida.wav --highpass 200 --lowpass 5000 --limit 0.6
coil-prep prep minhas_musicas/ -o saida/           # lote (mp3, wav, flac, ogg, m4a)
coil-prep check saida.wav                          # exit code 1 se pico > -3 dBFS ou clipping
```

| Preset | Passa-alta | Passa-baixa | Limite |
|---|---|---|---|
| `default` | 150 Hz | 8000 Hz | 0.7 (~ -3 dBFS) |
| `vocal` | 200 Hz | 4000 Hz | 0.7 |
| `safe` | 150 Hz | 6000 Hz | 0.5 (~ -6 dBFS) |

O `prep` sempre mede o resultado ao final e avisa se o pico passou de -3 dBFS. Saída: WAV 16-bit mono, 44,1 kHz (`--rate` altera). O `check` decodifica via ffmpeg, então lê qualquer formato.

## ⚠️ Segurança

**Comece com o volume do notebook BAIXO** e aumente aos poucos com a bobina ligada. Alta tensão e arco de plasma: siga as instruções do fabricante, mantenha longe de crianças, eletrônicos e materiais inflamáveis.

## Desenvolvimento

```bash
pip install -e ".[dev]"
ruff check . && pytest
```
Os testes geram áudio sintético; **nunca commite música ou áudio com copyright** (o `.gitignore` bloqueia mp3/wav/flac/mid/ogg).

## Créditos

- [Demucs](https://github.com/adefossez/demucs) (MIT), separação de stems (planejado)
- [Basic Pitch](https://github.com/spotify/basic-pitch) (Apache-2.0, Spotify), áudio → MIDI (planejado)
- [midi2tesla](https://github.com/yspacedev/midi2tesla): referência de ideia, nenhum código foi reutilizado

## Licença

[MIT](LICENSE)
