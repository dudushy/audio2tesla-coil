FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 openssl git git-lfs ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Deno: o yt-dlp precisa de um runtime JavaScript externo para resolver os desafios do YouTube
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app

# A ordem das layers importa: o Docker reaproveita uma layer só se ela e todas as anteriores
# não mudaram. Por isso as dependências pesadas ficam ANTES do código-fonte; editar algo em src/
# só refaz as duas últimas linhas (segundos). O cache do pip (--mount) sobrevive mesmo quando uma
# layer é invalidada, então um rebuild das deps reaproveita os downloads.
#
# Opções de build (veja o README):
#   WITH_SEPARATE=0   não instala Demucs/PyTorch (economiza ~300 MB de download + ~80 MB de modelo)
#   PIP_INDEX_URL     mirror do PyPI (rede lenta/bloqueada), ex.: https://pypi.tuna.tsinghua.edu.cn/simple
#   TORCH_INDEX_URL   mirror dos wheels CPU do PyTorch
ARG WITH_SEPARATE=1
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu
# redes lentas derrubam conexões longas: timeout maior e mais tentativas
ENV PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=10

# 1) PyTorch só CPU (sem GPU NVIDIA), apenas se o Demucs for instalado.
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ "$WITH_SEPARATE" = "1" ]; then \
      pip install "torch<2.9" "torchaudio<2.9" --index-url "$TORCH_INDEX_URL"; \
    fi

# 2) Dependências do projeto, lidas do pyproject.toml (fonte única) sem precisar do src/.
#    Basic Pitch roda via ONNX: dependências em `midi-onnx` + basic-pitch com --no-deps,
#    o que evita o TensorFlow (~2 GB instalado, ~475 MB de download).
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    WITH_SEPARATE="$WITH_SEPARATE" python -c "import os, tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; \
e = p['optional-dependencies']; r = p['dependencies'] + e['midi-onnx'] \
+ (e['separate'] if os.environ['WITH_SEPARATE'] == '1' else []); print('\n'.join(r))" \
    > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt \
    && pip install --no-deps "basic-pitch==0.4.0"

# 2b) Separação e transcrição mais fortes (só com WITH_SEPARATE=1, porque precisam do PyTorch):
#   - audio-separator (Mel-Band RoFormer, voz): instalado sem as dependências que não usamos e
#     que quebrariam o build (diffq precisa de compilador; onnx2torch puxaria outro PyTorch);
#   - mt3-infer (YourMT3+), só com WITH_MT3=1. transformers fixo em 4.43.4: o código do YourMT3+
#     usa model_parallel_utils (removido na 5.x) e a API do T5 de antes do cache_position (4.44+).
#   O arquivo de restrições trava o torch/torchaudio/numpy já instalados: nada é trocado.
#   Os modelos (RoFormer ~0,9 GB, YourMT3+ 536 MB, RMVPE 362 MB) baixam na 1ª vez, no volume /cache.
ARG WITH_MT3=1
RUN --mount=type=cache,target=/root/.cache/pip \
    if [ "$WITH_SEPARATE" = "1" ]; then \
      pip freeze | grep -iE '^(torch|torchaudio|numpy)==' > /tmp/constraints.txt \
      && pip install --no-deps "audio-separator==0.48.0" \
      && pip install -c /tmp/constraints.txt "beartype>=0.18.5,<0.19" ml_collections pydub \
           "rotary-embedding-torch>=0.6.1,<0.7" "samplerate==0.1.0" "einops>=0.7" julius \
      && if [ "$WITH_MT3" = "1" ]; then \
           pip install -c /tmp/constraints.txt --extra-index-url "$TORCH_INDEX_URL" "mt3-infer==0.2.0" "transformers==4.43.4"; \
         fi; \
    fi

# 3) Código do projeto: leve, as dependências já estão instaladas.
COPY README.md ./
COPY src ./src
RUN pip install --no-deps .

# dados (entrada, stems, saída, device.json) e cache dos pesos do Demucs ficam em volumes
ENV COIL_DATA=/data TORCH_HOME=/cache/torch XDG_CACHE_HOME=/cache
VOLUME ["/data", "/cache"]
EXPOSE 1337
# HTTP e HTTPS na mesma porta (o microfone do navegador exige HTTPS fora do localhost)
CMD ["python", "-m", "coil_prep.web.serve"]
