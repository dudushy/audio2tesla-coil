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
| `calibrate`: tons de teste + perfil da bobina (faixa mín/máx de Hz) | ✅ |
| `midi`: melodia de `.mid` ou áudio → onda quadrada monofônica, dentro do perfil | ✅ (experimental) |
| `--separate`: isola um stem com Demucs antes do `prep`/`midi` | ✅ (experimental) |

## Instalação

Requisitos: Python 3.10+ e [ffmpeg](https://ffmpeg.org/) no `PATH`.

**Linux** (bash/zsh) ou **WSL / Ubuntu**
```bash
sudo apt install ffmpeg python3-venv
git clone https://github.com/dudushy/audio2tesla-coil
cd audio2tesla-coil
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
```

**PowerShell (Windows)**
```powershell
winget install Gyan.FFmpeg        # abra um novo terminal depois
git clone https://github.com/dudushy/audio2tesla-coil
cd audio2tesla-coil
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

Dica: o áudio sai pelo P2 do sistema onde você toca o WAV; toque no mesmo sistema onde gerou, ou copie o arquivo.

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

## Modo melodia (`midi`): o caminho principal

Filtrar uma mixagem cheia (`prep`) deixa bateria, baixo e sintes dentro da faixa, e a bobina toca **uma nota por vez**, então vira ruído. O `midi` extrai só a **melodia**, como alguém murmurando:

```
.mid ou áudio → notas → a mais aguda de cada instante ("skyline") → move de oitava para a faixa da bobina → onda quadrada
```
```bash
coil-prep midi musica.mid -o saida.wav --profile device.json   # .mid: leve, só numpy + mido
pip install -e ".[midi]"                                        # extra p/ áudio (Basic Pitch)
coil-prep midi musica.mp3 -o saida.wav --profile device.json
```
Opções: `--voices N` (padrão 1), `--min-note S` (descarta notas menores que S segundos, padrão 0,06), `--gap S` (silêncio entre notas coladas ou repetidas, padrão 0,02), `--min-hz/--max-hz` (sobrescrevem o perfil; sem perfil usa 100–800 Hz), `--peak-db` (padrão -8).

- Uma faixa de 3 oitavas (como 100–800 Hz) sempre comporta qualquer nota movendo de oitava, sem perder a melodia; faixas com menos de 1 oitava dão erro.
- **Limitação:** em mixagem cheia a transcrição erra e "a nota mais aguda" nem sempre é a melodia. Um `.mid` de melodia costuma dar resultado melhor; use `--separate` para isolar um stem antes (abaixo). O `midi` avisa quando mais de 30% do tempo tem notas sobrepostas, sinal de que a melodia pode sair errada.
- **Extra `[midi]` (Basic Pitch):** no Linux exige **Python 3.10 ou 3.11** (as dependências `tensorflow<2.15.1` e `tflite-runtime` não têm wheel para 3.12+). O modo `.mid` funciona em qualquer Python 3.10+.

### Isolar um stem antes (`--separate`)

```bash
pip install -e ".[separate]"        # Demucs + PyTorch (vários GB); o modelo baixa na 1ª execução
coil-prep midi musica.mp3 -o saida.wav --profile device.json --separate                  # stem "other" (padrão)
coil-prep midi musica.mp3 -o saida.wav --profile device.json --separate --stem vocals
```
Roda em **CPU**, então leva alguns minutos por música. O stem fica em cache em `separated/` (ignorado pelo git): rodar de novo na mesma música é rápido. `--stem` aceita `other` (padrão: sintes e instrumentos, onde costuma estar a melodia em música eletrônica), `vocals` (melhor para música cantada), `bass` e `drums`. Também funciona no `prep`. Para o extra `[midi]` e o `[separate]` juntos, use Python 3.10 ou 3.11. Dica para economizar disco: instale antes `pip install torch --index-url https://download.pytorch.org/whl/cpu`.

## Calibragem da bobina

Não adianta gerar frequências que a bobina não toca. Descubra a faixa de ouvido:
```bash
coil-prep calibrate tones -o calibrate.wav     # tons quadrados, volume baixo (-8 dBFS)
# toque calibrate.wav na bobina (comece com volume BAIXO) e anote quais tons soam como nota clara
coil-prep calibrate save --min 200 --max 3000  # grava device.json (ignorado pelo git)
coil-prep prep entrada.mp3 -o saida.wav --profile device.json   # filtros = min/max do perfil
```
Prioridade dos cortes: `--highpass/--lowpass` > `--profile` > `--preset`.

## ⚠️ Segurança

**Comece com o volume do notebook BAIXO** e aumente aos poucos com a bobina ligada. Alta tensão e arco de plasma: siga as instruções do fabricante, mantenha longe de crianças, eletrônicos e materiais inflamáveis.

## Desenvolvimento

```bash
pip install -e ".[dev]"
ruff check . && pytest
```
Os testes geram áudio sintético; **nunca commite música ou áudio com copyright** (o `.gitignore` bloqueia mp3/wav/flac/mid/ogg).

## Créditos

- [Demucs](https://github.com/adefossez/demucs) (MIT), separação de stems (`--separate`)
- [Basic Pitch](https://github.com/spotify/basic-pitch) (Apache-2.0, Spotify), áudio → MIDI (planejado)
- [midi2tesla](https://github.com/yspacedev/midi2tesla): referência de ideia, nenhum código foi reutilizado

## Licença

[MIT](LICENSE)
