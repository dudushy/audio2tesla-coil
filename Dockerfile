FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Deno: o yt-dlp precisa de um runtime JavaScript externo para resolver os desafios do YouTube
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app

# A ordem das layers importa: o Docker reaproveita uma layer só se ela e todas as anteriores
# não mudaram. Por isso as dependências pesadas (torch, tensorflow, demucs) ficam ANTES do
# código-fonte; editar algo em src/ só refaz as duas últimas linhas (segundos).
# O cache do pip (--mount) também sobrevive quando uma layer é invalidada, então mesmo um
# rebuild das deps reaproveita os downloads em vez de baixar ~475 MB de novo.

# 1) PyTorch só CPU (sem GPU NVIDIA); só muda se esta linha mudar.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install "torch<2.9" "torchaudio<2.9" --index-url https://download.pytorch.org/whl/cpu

# 2) Dependências do projeto, lidas do pyproject.toml (fonte única) sem precisar do src/.
#    Só é refeita se o pyproject.toml mudar.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -c "import tomllib; p = tomllib.load(open('pyproject.toml', 'rb'))['project']; \
print('\n'.join(p['dependencies'] + p['optional-dependencies']['midi'] + p['optional-dependencies']['separate']))" \
    > /tmp/requirements.txt \
    && pip install -r /tmp/requirements.txt

# 3) Código do projeto: leve, as dependências já estão instaladas.
COPY README.md ./
COPY src ./src
RUN pip install --no-deps .

# dados (entrada, stems, saída, device.json) e cache dos pesos do Demucs ficam em volumes
ENV COIL_DATA=/data TORCH_HOME=/cache/torch XDG_CACHE_HOME=/cache
VOLUME ["/data", "/cache"]
EXPOSE 8000
CMD ["uvicorn", "coil_prep.web.app:create_default_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
