"""Baixa o áudio de uma URL (YouTube etc.) com yt-dlp e converte para WAV (e MP3, opcional)."""

from pathlib import Path
from urllib.parse import urlparse

DEFAULT_DIR = Path("input")
RATE = 44100
BOT_CHECK = "Sign in to confirm"


class FetchError(RuntimeError):
    pass


def is_url(text: str) -> bool:
    parsed = urlparse(text)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _load_ydl():
    try:
        from yt_dlp import YoutubeDL
    except ImportError as e:
        raise FetchError(
            'yt-dlp não instalado. Instale o extra: pip install -e ".[youtube]"'
        ) from e
    return YoutubeDL


def fetch(url: str, out_dir: Path = DEFAULT_DIR, ydl_cls=None, cookies: Path | None = None) -> Path:
    """Baixa o melhor áudio de `url` para `out_dir` como WAV 44,1 kHz; devolve o caminho do WAV.

    WAV é o formato de trabalho: converter de novo para MP3 adicionaria mais uma geração de perda
    (a fonte do YouTube já é Opus/AAC).
    """
    if not is_url(url):
        raise FetchError(f"não é uma URL http(s): {url}")
    out_dir.mkdir(parents=True, exist_ok=True)
    ydl_cls = ydl_cls or _load_ydl()
    postprocessors = [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}]
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(out_dir / "%(title).80s [%(id)s].%(ext)s"),
        "restrictfilenames": True,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": postprocessors,
        "postprocessor_args": {"extractaudio": ["-ar", str(RATE)]},
    }
    if cookies is not None and cookies.is_file():
        opts["cookiefile"] = str(cookies)  # exportado do navegador pelo usuário (ver README)
    try:
        with ydl_cls(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if info is None:
                raise FetchError("yt-dlp não retornou informações do vídeo")
            wav = Path(ydl.prepare_filename(info)).with_suffix(".wav")
    except FetchError:
        raise
    except Exception as e:  # yt-dlp levanta DownloadError e variantes
        msg = f"falha ao baixar: {e}"
        if BOT_CHECK in str(e):
            msg += (
                "\nO YouTube pediu confirmação anti-bot. Exporte os cookies do YouTube para "
                "data/cookies.txt (passo a passo no README) e tente de novo."
            )
        raise FetchError(msg) from e
    if not wav.exists():
        raise FetchError(f"WAV esperado não foi criado: {wav}")
    return wav
