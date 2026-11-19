FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
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

# 3) Código do projeto: leve, as dependências já estão instaladas.
COPY README.md ./
COPY src ./src
RUN pip install --no-deps .

# dados (entrada, stems, saída, device.json) e cache dos pesos do Demucs ficam em volumes
ENV COIL_DATA=/data TORCH_HOME=/cache/torch XDG_CACHE_HOME=/cache
VOLUME ["/data", "/cache"]
EXPOSE 8000
CMD ["uvicorn", "coil_prep.web.app:create_default_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
