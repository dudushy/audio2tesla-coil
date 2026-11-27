# coil-prep

🇧🇷 Português · [🇬🇧 English](README.en.md)

App web (roda em Docker) que transforma uma música (link do YouTube ou arquivo) na **melodia em onda quadrada**, pronta para tocar em um **plasma speaker** (mini bobina de Tesla musical) ligado por cabo P2 (3,5 mm) a um notebook.

## O que é e o que não é

A bobina toca **uma nota por vez** como onda quadrada, de baixa fidelidade. Por isso o app não "limpa" a mixagem: ele extrai só a **melodia**, como alguém murmurando, e a gera em onda quadrada dentro da faixa de frequência que a bobina toca bem.

```
link ou arquivo → (trecho início/fim) → (Demucs: isola um stem) → notas (Basic Pitch ou .mid) → 1 voz por instante
→ áudio: ritmo encaixado na grade do tempo da música → música inteira transposta em oitavas para o meio da faixa
→ oitava pelo contorno dentro da faixa da bobina → onda quadrada 50% (pico -8 dBFS) → WAV + MIDI
```

**Áudio (não .mid):** a transcrição erra o início das notas em dezenas de ms e quebra notas ao acaso, o que numa onda quadrada soa como ritmo torto. Se a música tem pulso firme, o app mede o tempo (rastreador de batidas do librosa, refinado pelos inícios das próprias notas) e encaixa a melodia numa grade de 140–280 ms (colcheia a 126 BPM): uma nota por célula, sempre começando na grade; nota longa vira notas repetidas, como nos arranjos para bobina. Notas do baixo que vazam (mais de 15 semitons abaixo da melodia) são ignoradas. Sem pulso firme (rubato), as notas ficam como transcritas.

**Limitações:** não é hi-fi; em mixagem cheia a transcrição erra e a nota mais aguda nem sempre é a melodia (o app avisa quando mais de 30% do tempo tem notas sobrepostas). Um `.mid` de melodia costuma dar o melhor resultado.

## Como usar

Requisitos: [Docker](https://docs.docker.com/engine/install/) com Compose.

```bash
git clone https://github.com/dudushy/audio2tesla-coil && cd audio2tesla-coil
docker compose up --build
```

Ou use `./start.sh` (Linux, macOS, WSL): sobe em segundo plano e **mostra o endereço para copiar** em outros aparelhos da rede. `./start.sh --ip` só mostra os endereços.

**Instalação lenta ou sem rede boa?** O build não baixa mais o TensorFlow: o Basic Pitch roda via ONNX (`onnxruntime`), o mesmo modelo e resultado, ~500 MB instalados em vez de ~2 GB. O maior download restante é o PyTorch (CPU) do Demucs. Opções de build, por variável de ambiente ou arquivo `.env` ao lado do `docker-compose.yml`:

```bash
WITH_SEPARATE=0 docker compose build          # sem Demucs/PyTorch (só 'mix' e .mid; ~300 MB a menos)
PIP_INDEX_URL=https://seu-mirror/simple TORCH_INDEX_URL=https://seu-mirror/whl/cpu docker compose build
```

Com `WITH_SEPARATE=0` a opção de separar stems falha com uma mensagem clara; refaça o build com `WITH_SEPARATE=1` para ter de volta.

Abra **http://localhost:1337** no navegador do notebook ligado à bobina (ou, de outro aparelho da mesma rede, `http://<IP-do-notebook>:1337`; o `./start.sh --ip` mostra o IP). O app tem 5 telas, na barra de navegação:

- **Gerar** (`/`): três fontes. **🎤 Gravar**: cantarole, assobie ou grave uma música tocando e o app converte como o áudio de um link (**cantarolar/assobiar** mede a frequência de cada nota direto na voz, sem transcritor de música, sem grade rítmica e sem arredondar para o semitom: a nota sai na mesma altura e afinação em que foi cantada; **música tocando** usa as três variações, como um link; até 10 min). Pelo IP da rede o app abre em HTTPS (veja **HTTPS** abaixo), necessário para o microfone. Ou cole o link ou envie um arquivo (áudio, `.mid` ou `.nbs` do Minecraft/Open Note Block Studio, até 100 MB; "Buscar MIDI no BitMidi" lista resultados do bitmidi.com, mas você baixa o `.mid` e o envia, porque o robots.txt dele proíbe `/uploads/`). Em **Opções**, escolha uma ou várias **formas de conversão** — **Transcrição de notas** (padrão), **Afinação exata** (voz ou instrumento solo, só áudio), **Estilo Note Block** (como os conversores de músicas para Minecraft: uma batida a cada 0,1 s, nas 25 notas do note block, altura por FFT com soma harmônica) e **Todas as trilhas** (só `.mid`/`.nbs`) — e as **partes** do áudio (stems `other`, `vocals`, `mix`); cada parte é gerada em cada forma marcada e, se quiser, um **trecho** (`1:30` ou `90`). **Uma ação gera todas**: elas entram na fila (1 por vez, o Demucs usa CPU) e cada cartão aparece lado a lado com player, avisos, parecença medida e 👍/👎. A ★ marca o stem com a melhor média no seu histórico. Com resultados na tela, eles ficam no topo e o formulário recolhe em **Nova geração**. O lote fica aberto ao voltar da edição (`/?batch=<id>`; some se o servidor reiniciar).
- **Faixa** (`/track/<id>`): abre ao clicar em "Abrir no editor". Piano roll para editar (arraste para mover, bordas para a duração, retângulo para selecionar, duplo clique/Desenhar para criar, Ctrl+C/V/D, Delete, Ctrl+Z, Espaço; "Como editar" lista tudo; no celular, pinça e toque longo), "Aplicar e ouvir", velocidade, estilo da onda, trilha do `.mid`, original, exportar WAV/MP3/MIDI/NBS e avaliação. As edições são registradas como comparação (gerado x final) em `data/feedback.jsonl`.
- **Partituras** (`/scores`): melodia direto das notas, sem separar nem transcrever áudio. No topo, **Buscar em todos os acervos** pesquisa de uma vez, em paralelo, 6 fontes e mostra de onde veio cada resultado e o que ele é: Lakh MIDI Dataset (MIDI, CC-BY 4.0), MIDIWorld (MIDI), MC Utils (`.nbs`), Note Block World (`.nbs`, só link: baixar exige login no site), Mutopia (partituras clássicas) e BitMidi (MIDI, só link: o site proíbe download automático). ▶ baixa e gera; ↗ abre o site. Uma fonte fora do ar não derruba as outras. Abaixo, as abas exploram por categoria. A aba **Pop e rock** busca por artista ou música em ~17 mil MIDIs do subconjunto "clean" do [Lakh MIDI Dataset](https://colinraffel.com/projects/lmd/) (Colin Raffel, CC-BY 4.0; cópia com índice em `ryohey/lakh-midi` no GitHub, baixada um arquivo por vez), forte em pop e rock até ~2010; um toque baixa e gera as variações, e há link de busca no FreeMIDI para o que não estiver lá. A aba **Jogos e Minecraft** busca primeiro no acervo de músicas de Minecraft do [MC Utils](https://mcutils.com/note-block-songs) (~550 arranjos `.nbs` do repositório público `flytegg/nbs-songs`; um toque baixa e gera as variações). Ela também busca no BitMidi (com atalhos como Mario, Zelda e Sonic), aponta o NinSheetMusic, o VGMusic e o Note Block World (versões de Minecraft em `.nbs`) e recebe o `.mid` que você baixou, gerando na hora (o app não baixa arquivos desses sites; músicas protegidas são só para uso pessoal). Na aba **Clássicas**, no topo fica a busca no [Mutopia Project](https://www.mutopiaproject.org/) (domínio público e Creative Commons; a licença aparece em cada peça) e, abaixo, uma lista de músicas famosas (Für Elise, Ode à Alegria, Marcha Turca, Tocata e Fuga e outras, com a trilha da melodia já escolhida). O app baixa o `.mid` gerado da partitura (em peças com vários movimentos você escolhe um) e o ajusta às notas calibradas; o resultado abre em **Gerar** e no editor. O catálogo é majoritariamente clássico e tradicional.
- **Soundpad** (`/soundpad`): grade que ocupa a tela toda com uma pad por nota (C1–B8, 96 notas), uma cor por oitava (grave embaixo; no celular em pé a grade vira 6 colunas). Toca onda quadrada direto no navegador, no mesmo nível do WAV (-8 dBFS, duty 50%); com **Ajustar à faixa da bobina** marcado só aparecem as notas calibradas (desmarcado, todas; as de fora ficam listradas). Várias pads ao mesmo tempo (multitoque ou várias teclas; a bobina foi pensada para 1 nota, teste acordes com volume baixo); arraste para deslizar; no teclado do computador, A W S E D F T G Y H U J K O L tocam e Z/X trocam a oitava. **Instrumento** alterna entre **Pads** e **Piano** (teclado em fileiras de oitavas, grave embaixo). **Toque mínimo** (padrão 0,3 s) faz um toque rápido soar por tempo suficiente para Bluetooth e caixas não engolirem. **⛶** deixa na tela só o instrumento e os botões. **Gravar / Reproduzir** registra o que você toca e **exporta WAV, MP3, MIDI ou NBS** pelo servidor, com a mesma render do pipeline (acordes preservados, -8 dBFS, silêncio inicial cortado). "Ajustar à faixa da bobina" também dobra por oitavas, na exportação, as notas fora da faixa calibrada; desmarcado, exporta como tocado. O MP3 usa o ffmpeg do container (com perdas; para a bobina prefira WAV).
- **Calibrar** (`/calibrate`): cada nota de C1 a B8 toca por 2 s, com 3 s de pausa antes da seguinte, e você responde **se ouviu na bobina** (Sim/Não, teclas S/N, R repete; "Terminar aqui" encerra quando acabar o som). Revise as notas e salve: vai para `data/device.json`, e o app passa a usar essas notas (pelo menos 1 oitava entre a mais grave e a mais aguda). Dá para informar a faixa em Hz manualmente. Sem calibragem vale 100–800 Hz.

Avaliações (👍/👎 com motivo) ficam em `data/feedback.jsonl`; o app sugere o stem de melhor média, sem machine learning.

- **.mid com várias trilhas:** a melodia costuma passar de um instrumento para outro (riff, voz, ponte). No modo **Automático**, a trilha que mais parece a melodia (toca bastante, não é só acorde, registro médio/agudo, variedade de notas) manda e as outras que também parecem melodia só **preenchem as pausas de 1 s ou mais** dela (baixo e pads de acordes ficam de fora); nunca interrompem a principal e o resultado segue em 1 voz. O seletor "Trilha do MIDI" mostra quantos segundos cada trilha entrou e permite escolher uma só ou "todas misturadas". A faixa calibrada **não descarta notas**: só dobra de oitava; o que some são pausas em que nenhuma trilha de melodia toca e trechos < 60 ms.

- **Verificação objetiva:** `python -m coil_prep.verify musica.mid --min-hz 100 --max-hz 600` gera o WAV e mede, no áudio final, a frequência que soa em cada nota contra a música original: % com som, nota certa (qualquer oitava), nota e oitava certas, contorno (sobe/desce igual), saltos de oitava falsos, afinação em cents e se a nota existe na harmonia. Referência = a voz mais aguda de cada instante. Use para validar qualquer mudança no tratamento das notas. Para comparar com um **resultado esperado** (um áudio que já é a melodia em onda quadrada): `python -m coil_prep.evaluate esperado.mp3 gerado.wav` confere a tocabilidade do WAV (1 voz, faixa, duty 50%, pico -8 dBFS) e mede F1 por nota (início ±50 ms, altura ±50 cents, via `mir_eval`), erro de oitava e % do tempo com a nota certa.

O áudio toca **no navegador**, então sai pela saída P2 do notebook, a mesma da bobina. **Comece com o volume BAIXO.**

- O 1º uso baixa os pesos do Demucs; a separação roda em **CPU** e leva alguns minutos por música. O resultado fica em cache: testar outro stem do mesmo arquivo é mais rápido.
- Os dados ficam em `./data` (`input/`, `separated/`, `output/`; ignorados pelo git). Os pesos do Demucs ficam em um volume Docker (`coil-cache`).
- **Melodia de várias trilhas:** a trilha principal fica inteira e as pausas dela (1 s ou mais) são preenchidas por outras vozes de melodia, mesmo que toquem pouco; nas pausas longas (2 s ou mais) que ainda sobrarem entra o baixo. Acordes e pads nunca entram.
- **Downloads** do editor saem com nome legível (ex.: `A Little Piece of Heaven - estilo Note Block.nbs`).
- **Arquivos `.nbs` (Minecraft):** cada camada vira uma trilha, como num `.mid`; instrumentos com oitava própria (contrabaixo, flauta, sino...) são corrigidos e percussão fica de fora. O `.nbs` exportado usa harpa, 10 tiques por segundo e as 25 notas do note block (o que não cabe muda de oitava).
- **Altura original:** a música só muda de oitava se não couber na faixa calibrada (e então o mínimo necessário).
- **Notas da bobina:** ao gerar, cada nota é movida por oitavas para uma nota ouvida na calibragem (se nenhuma oitava dela foi ouvida, fica dentro da faixa). O editor de faixa permite qualquer nota entre a mais grave e a mais aguda.
- **Rede local:** o compose publica a porta **1337** em `0.0.0.0`, então celulares/PCs da mesma rede acessam o app. A API não tem autenticação e baixa vídeos a pedido: use só em rede confiável e não faça port-forward no roteador. Para voltar a só-local: `COIL_BIND=127.0.0.1 docker compose up -d`. Se o firewall do host bloquear (ex.: `sudo ufw allow 1337/tcp`), libere a porta.
- **HTTPS:** a porta 1337 atende `http` e `https`. Abrir uma página por `http://<IP>:1337` leva a `https://<IP>:1337`, porque o navegador só libera o microfone em endereço seguro (`http://localhost:1337` continua em HTTP). O certificado é autoassinado, criado na 1ª execução em `data/tls/`: cada aparelho mostra um aviso na primeira vez (**Avançado → Continuar**). Se o IP mudar, o `./start.sh` gera outro e o aviso volta. Para desativar: `COIL_HTTPS=0`.
- **Windows + WSL em modo de rede `mirrored`:** `localhost` funciona, mas o firewall do WSL bloqueia os outros aparelhos. Libere uma vez com `./start.sh --liberar-firewall` (abre o aviso de administrador do Windows; a regra vale só para a porta TCP 1337) ou rode num PowerShell como administrador: `New-NetFirewallHyperVRule -Name 'audio2tesla-coil-1337' -DisplayName 'audio2tesla-coil (1337)' -Direction Inbound -VMCreatorId '{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}' -Protocol TCP -LocalPorts 1337 -Action Allow`. Para desfazer: `Remove-NetFirewallHyperVRule -Name 'audio2tesla-coil-1337'`. Teste de **outro** aparelho: nesse modo o próprio PC pode não abrir o IP da rede.
- Áudio sai no aparelho que abriu a página, não no servidor.

### Estilo da onda (timbre)

Uma quadrada monofônica tem timbre fixo; o que dá para variar é o **duty** (largura do pulso, muda os harmônicos) e a **articulação** (quanto de cada nota soa: legato x staccato). Depois de gerar, o app mede a parecença do WAV com o original (o áudio/stem, ou as notas do `.mid`): nota certa, início das notas, envelope de energia e brilho (centroide espectral), e escolhe o melhor entre alguns presets. O padrão é sempre o clássico (quadrada de 50%, a onda que a bobina já tocou bem); o preset "Automático" usa o melhor score da busca e os demais ficam para experimentar em "Estilo da onda". O score é um proxy medido no computador, **não** substitui o teste de ouvido (duty ≠ 50% e notas encurtadas ainda não foram validados na bobina). Código: `src/coil_prep/fidelity.py`.

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
