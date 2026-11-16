FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Deno: o yt-dlp precisa de um runtime JavaScript externo para resolver os desafios do YouTube
COPY --from=denoland/deno:bin /deno /usr/local/bin/deno

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

# PyTorch só CPU (sem GPU NVIDIA); depois o resto resolve contra o torch já instalado.
RUN pip install --no-cache-dir "torch<2.9" "torchaudio<2.9" --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir ".[midi,separate]"

# dados (entrada, stems, saída, device.json) e cache dos pesos do Demucs ficam em volumes
ENV COIL_DATA=/data TORCH_HOME=/cache/torch XDG_CACHE_HOME=/cache
VOLUME ["/data", "/cache"]
EXPOSE 8000
CMD ["uvicorn", "coil_prep.web.app:create_default_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
