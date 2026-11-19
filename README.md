# coil-prep

🇧🇷 Português · [🇬🇧 English](README.en.md)

App web (roda em Docker) que transforma uma música (link do YouTube ou arquivo) na **melodia em onda quadrada**, pronta para tocar em um **plasma speaker** (mini bobina de Tesla musical) ligado por cabo P2 (3,5 mm) a um notebook.

## O que é e o que não é

A bobina toca **uma nota por vez** como onda quadrada, de baixa fidelidade. Por isso o app não "limpa" a mixagem: ele extrai só a **melodia**, como alguém murmurando, e a gera em onda quadrada dentro da faixa de frequência que a bobina toca bem.

```
link ou arquivo → (Demucs: isola um stem) → notas (Basic Pitch ou .mid) → 1 voz por instante
→ transposição da música inteira para caber na faixa (prefere só oitavas) → oitava pelo contorno dentro da faixa da bobina → onda quadrada (pico -8 dBFS) → WAV
```

**Limitações:** não é hi-fi; em mixagem cheia a transcrição erra e a nota mais aguda nem sempre é a melodia (o app avisa quando mais de 30% do tempo tem notas sobrepostas). Um `.mid` de melodia costuma dar o melhor resultado.

## Como usar

Requisitos: [Docker](https://docs.docker.com/engine/install/) com Compose.

```bash
git clone https://github.com/dudushy/audio2tesla-coil && cd audio2tesla-coil
docker compose up --build
```

**Instalação lenta ou sem rede boa?** O build não baixa mais o TensorFlow: o Basic Pitch roda via ONNX (`onnxruntime`), o mesmo modelo e resultado, ~500 MB instalados em vez de ~2 GB. O maior download restante é o PyTorch (CPU) do Demucs. Opções de build, por variável de ambiente ou arquivo `.env` ao lado do `docker-compose.yml`:

```bash
WITH_SEPARATE=0 docker compose build          # sem Demucs/PyTorch (só 'mix' e .mid; ~300 MB a menos)
PIP_INDEX_URL=https://seu-mirror/simple TORCH_INDEX_URL=https://seu-mirror/whl/cpu docker compose build
```

Com `WITH_SEPARATE=0` a opção de separar stems falha com uma mensagem clara; refaça o build com `WITH_SEPARATE=1` para ter de volta.

Abra **http://localhost:8000** no navegador do notebook ligado à bobina:

1. Cole o link do vídeo ou envie um arquivo (áudio ou `.mid`, até 100 MB). Na aba Arquivo, "Buscar MIDI no BitMidi" lista resultados do bitmidi.com: abra a página, baixe o `.mid` e envie-o. O BitMidi não tem API e o robots.txt dele proíbe `/uploads/`, então o app só lê as páginas de busca (cache de 10 min, 1 req/s) e não baixa os arquivos por você.
2. Escolha o **som a usar**: `other` (padrão: sintes e instrumentos, onde costuma estar a melodia em música eletrônica), `vocals` (música cantada) ou `mix` (sem separar).
3. Clique em **Gerar**, ouça o resultado e o original ali mesmo e, se quiser, baixe o **WAV** (recomendado) ou o **MP3** (o codec com perdas pode arredondar um pouco as quinas da onda quadrada).
4. **Edite as notas** (opcional) no piano roll: arraste para mover no tempo e no tom, arraste as bordas para mudar a duração, arraste no vazio para selecionar várias (retângulo), duplo clique ou a ferramenta Desenhar criam notas, Ctrl+C/V/D copiam e duplicam, Delete exclui, Ctrl+Z desfaz, Espaço toca/pausa; há grade ajustável e, no celular, pinça para zoom e segurar+arrastar para selecionar (o botão "Como editar" lista todos os atalhos). As notas tracejadas mostram o que o app gerou e você mudou. O tom fica sempre dentro da faixa calibrada e a bobina toca uma nota por vez (sobreposição é cortada). Clique em **Aplicar e ouvir** para re-renderizar o áudio e exporte o WAV/MP3.
5. Ao exportar (ou avaliar) depois de editar, o app registra a **comparação técnica**: o que ele gerou x o que você deixou (notas apagadas, adicionadas e movidas, com as variações de tom e tempo). Vai para `data/feedback.jsonl` (evento `edit`) e as listas completas para `data/feedback/<job>.json`.
6. Toque na bobina e **avalie** (👍/👎, com um motivo no 👎). As avaliações ficam em `data/feedback.jsonl` e o app passa a **sugerir o som** (stem) com a melhor média no seu histórico. Isso é só uma média por stem, sem machine learning: um modelo para escolher a nota da melodia só faria sentido com dezenas de avaliações.

7. **Tocar ao vivo:** o cartão "Tocar ao vivo (teclado)" é um piano C2–B6 (a mesma faixa do teclado de teslacoil.app) que toca onda quadrada direto no navegador, no mesmo nível do WAV gerado (-8 dBFS, duty 50%), sem gerar nem enviar arquivo. Uma nota por vez; arraste para deslizar entre teclas; botões ◀ ▶ trocam a oitava; no teclado do computador, clique no piano e use A W S E D F T G Y H U J K O L (Z/X trocam a oitava). Teclas esmaecidas estão fora da faixa calibrada (tocam mesmo assim).
8. **.mid com várias trilhas:** a melodia costuma passar de um instrumento para outro (riff, voz, ponte). No modo **Automático**, a trilha que mais parece a melodia (toca bastante, não é só acorde, registro médio/agudo, variedade de notas) manda e as outras que também parecem melodia só **preenchem as pausas de 1 s ou mais** dela (baixo e pads de acordes ficam de fora); nunca interrompem a principal e o resultado segue em 1 voz. O seletor "Trilha do MIDI" mostra quantos segundos cada trilha entrou e permite escolher uma só ou "todas misturadas". A faixa calibrada **não descarta notas**: só dobra de oitava; o que some são pausas em que nenhuma trilha de melodia toca e trechos < 60 ms.

9. **Verificação objetiva:** `python -m coil_prep.verify musica.mid --min-hz 100 --max-hz 600` gera o WAV e mede, no áudio final, a frequência que soa em cada nota contra a música original: % com som, nota certa (qualquer oitava), nota e oitava certas, contorno (sobe/desce igual), saltos de oitava falsos, afinação em cents e se a nota existe na harmonia. Referência = a voz mais aguda de cada instante. Use para validar qualquer mudança no tratamento das notas.

O áudio toca **no navegador**, então sai pela saída P2 do notebook, a mesma da bobina. **Comece com o volume BAIXO.**

- O 1º uso baixa os pesos do Demucs; a separação roda em **CPU** e leva alguns minutos por música. O resultado fica em cache: testar outro stem do mesmo arquivo é mais rápido.
- Os dados ficam em `./data` (`input/`, `separated/`, `output/`; ignorados pelo git). Os pesos do Demucs ficam em um volume Docker (`coil-cache`).
- **Faixa da bobina:** sem calibragem o app usa 100–800 Hz. Abra **Calibrar a bobina** na página, toque os tons de teste (volume BAIXO), anote quais soam como nota clara e salve o menor e o maior Hz (a faixa precisa ter pelo menos 1 oitava). Fica em `data/device.json`.
- A porta fica só em `127.0.0.1`: a API não tem autenticação e baixa vídeos a pedido. Não a exponha na rede sem proteção.

### Baixar de links

O download usa o [yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense) e converte para WAV 44,1 kHz, o formato de trabalho (a fonte do YouTube já é Opus/AAC; passar por MP3 só adicionaria perda). Baixe só conteúdo que você tem direito de usar: os termos do YouTube restringem downloads fora dos meios oficiais. O yt-dlp quebra quando o YouTube muda; atualize com `docker compose build --no-cache`. Nunca versione áudio.

### "Sign in to confirm you're not a bot"

O YouTube às vezes exige login para baixar (comum em rede com IP "suspeito", como VPN, 4G ou datacenter). A solução é fornecer cookies do YouTube:

1. Abra uma **janela anônima**, faça login no YouTube e, **na mesma aba**, acesse `youtube.com/robots.txt` (deixe só essa aba anônima aberta).
2. Exporte os cookies de `youtube.com` no formato Netscape com uma extensão, como a **"Get cookies.txt LOCALLY"** (Chrome) ou a "cookies.txt" (Firefox). Cuidado: a extensão "Get cookies.txt" **sem** "LOCALLY" foi reportada como malware.
3. Feche a janela anônima (para a sessão nunca ser reaberta) e salve o arquivo como `data/cookies.txt`. O app o usa sozinho, sem reiniciar.

Avisos: (a) o yt-dlp avisa que usar sua conta pode causar **banimento temporário ou permanente**; prefira uma **conta secundária/descartável** e poucos downloads; (b) o arquivo dá acesso à sessão da conta, então **nunca o versione** (`data/` e `cookies.txt` já estão no `.gitignore`). Fontes: [FAQ](https://github.com/yt-dlp/yt-dlp/wiki/FAQ) e [Extractors](https://github.com/yt-dlp/yt-dlp/wiki/Extractors) do yt-dlp. Se ainda falhar, rode `docker compose build --no-cache` para pegar a versão mais nova do yt-dlp. Alternativa sem cookies: baixe o áudio por outro meio e **envie o arquivo** na aba "Arquivo".

## ⚠️ Segurança

Alta tensão e arco de plasma: siga as instruções do fabricante, mantenha longe de crianças, eletrônicos e materiais inflamáveis, e comece sempre com o volume do notebook baixo.

## Desenvolvimento

```bash
uv venv --python 3.11 && source .venv/bin/activate     # Basic Pitch exige Python 3.10 ou 3.11
uv pip install -e ".[dev]"                             # + ".[midi,separate]" para áudio e Demucs
ruff check . && pytest
COIL_DATA=./data uvicorn coil_prep.web.app:create_default_app --factory --reload
```

Extras: `[midi]` (Basic Pitch com TensorFlow; fixa `numpy<2` e `setuptools<81`), `[midi-onnx]` (as mesmas dependências sem TensorFlow; rode depois `pip install --no-deps basic-pitch==0.4.0`, como o Docker faz) e `[separate]` (Demucs + PyTorch, vários GB). Sem eles, `.mid` e `mix` funcionam. Os testes geram áudio sintético; **nunca commite música ou áudio com copyright** (o `.gitignore` bloqueia mp3/wav/flac/mid/ogg).

## Créditos

- [Demucs](https://github.com/adefossez/demucs) (MIT), separação de stems
- [Basic Pitch](https://github.com/spotify/basic-pitch) (Apache-2.0, Spotify), áudio → MIDI
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) (Unlicense), download de áudio
- [midi2tesla](https://github.com/yspacedev/midi2tesla): referência de ideia, nenhum código foi reutilizado

## Licença

[MIT](LICENSE)
