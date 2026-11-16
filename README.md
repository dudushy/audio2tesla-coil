# coil-prep

🇧🇷 Português · [🇬🇧 English](README.en.md)

App web (roda em Docker) que transforma uma música (link do YouTube ou arquivo) na **melodia em onda quadrada**, pronta para tocar em um **plasma speaker** (mini bobina de Tesla musical) ligado por cabo P2 (3,5 mm) a um notebook.

## O que é e o que não é

A bobina toca **uma nota por vez** como onda quadrada, de baixa fidelidade. Por isso o app não "limpa" a mixagem: ele extrai só a **melodia**, como alguém murmurando, e a gera em onda quadrada dentro da faixa de frequência que a bobina toca bem.

```
link ou arquivo → (Demucs: isola um stem) → notas (Basic Pitch ou .mid) → 1 voz por instante
→ oitava pelo contorno dentro da faixa da bobina → onda quadrada (pico -8 dBFS) → WAV
```

**Limitações:** não é hi-fi; em mixagem cheia a transcrição erra e a nota mais aguda nem sempre é a melodia (o app avisa quando mais de 30% do tempo tem notas sobrepostas). Um `.mid` de melodia costuma dar o melhor resultado.

## Como usar

Requisitos: [Docker](https://docs.docker.com/engine/install/) com Compose.

```bash
git clone https://github.com/dudushy/audio2tesla-coil && cd audio2tesla-coil
docker compose up --build
```

Abra **http://localhost:8000** no navegador do notebook ligado à bobina:

1. Cole o link do vídeo ou envie um arquivo (áudio ou `.mid`, até 100 MB).
2. Escolha o **som a usar**: `other` (padrão: sintes e instrumentos, onde costuma estar a melodia em música eletrônica), `vocals` (música cantada) ou `mix` (sem separar).
3. Clique em **Gerar**, ouça o resultado e o original ali mesmo e, se quiser, baixe o WAV.

O áudio toca **no navegador**, então sai pela saída P2 do notebook, a mesma da bobina. **Comece com o volume BAIXO.**

- O 1º uso baixa os pesos do Demucs; a separação roda em **CPU** e leva alguns minutos por música. O resultado fica em cache: testar outro stem do mesmo arquivo é mais rápido.
- Os dados ficam em `./data` (`input/`, `separated/`, `output/`; ignorados pelo git). Os pesos do Demucs ficam em um volume Docker (`coil-cache`).
- **Faixa da bobina:** sem calibragem o app usa 100–800 Hz. Para usar a sua, crie `data/device.json` com `{"min_hz": 100, "max_hz": 1000}` (uma tela de calibragem vem em um próximo PR). Faixas com menos de 1 oitava são recusadas.
- A porta fica só em `127.0.0.1`: a API não tem autenticação e baixa vídeos a pedido. Não a exponha na rede sem proteção.

### Baixar de links

O download usa o [yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense) e converte para WAV 44,1 kHz, o formato de trabalho (a fonte do YouTube já é Opus/AAC; passar por MP3 só adicionaria perda). Baixe só conteúdo que você tem direito de usar: os termos do YouTube restringem downloads fora dos meios oficiais. O yt-dlp quebra quando o YouTube muda; atualize com `docker compose build --no-cache`. Nunca versione áudio.

## ⚠️ Segurança

Alta tensão e arco de plasma: siga as instruções do fabricante, mantenha longe de crianças, eletrônicos e materiais inflamáveis, e comece sempre com o volume do notebook baixo.

## Desenvolvimento

```bash
uv venv --python 3.11 && source .venv/bin/activate     # Basic Pitch exige Python 3.10 ou 3.11
uv pip install -e ".[dev]"                             # + ".[midi,separate]" para áudio e Demucs
ruff check . && pytest
COIL_DATA=./data uvicorn coil_prep.web.app:create_default_app --factory --reload
```

Extras: `[midi]` (Basic Pitch; fixa `numpy<2` e `setuptools<81`) e `[separate]` (Demucs + PyTorch, vários GB). Sem eles, `.mid` e `mix` funcionam. Os testes geram áudio sintético; **nunca commite música ou áudio com copyright** (o `.gitignore` bloqueia mp3/wav/flac/mid/ogg).

## Créditos

- [Demucs](https://github.com/adefossez/demucs) (MIT), separação de stems
- [Basic Pitch](https://github.com/spotify/basic-pitch) (Apache-2.0, Spotify), áudio → MIDI
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense), download de áudio
- [midi2tesla](https://github.com/yspacedev/midi2tesla): referência de ideia, nenhum código foi reutilizado

## Licença

[MIT](LICENSE)
