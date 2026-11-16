import pytest

from coil_prep.cli import main
from coil_prep.fetch import FetchError, fetch, is_url


class FakeYDL:
    """Imita yt_dlp.YoutubeDL: 'baixa' criando o .wav que o pós-processador criaria."""

    last_opts = None

    def __init__(self, opts):
        FakeYDL.last_opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=True):
        from pathlib import Path

        out = Path(
            FakeYDL.last_opts["outtmpl"]
            .replace("%(title).80s", "musica")
            .replace("%(id)s", "abc")
            .replace("%(ext)s", "webm")
        )
        out.with_suffix(".wav").write_bytes(b"RIFF")
        return {"title": "musica", "id": "abc", "ext": "webm"}

    def prepare_filename(self, info):
        from pathlib import Path

        return str(
            Path(
                FakeYDL.last_opts["outtmpl"]
                .replace("%(title).80s", "musica")
                .replace("%(id)s", "abc")
                .replace("%(ext)s", "webm")
            )
        )


def test_is_url():
    assert is_url("https://www.youtube.com/watch?v=abc")
    assert not is_url("musica.mp3")
    assert not is_url("file:///etc/passwd")


def test_fetch_rejects_non_url(tmp_path):
    with pytest.raises(FetchError):
        fetch("musica.mp3", tmp_path, ydl_cls=FakeYDL)


def test_fetch_returns_wav_and_sets_options(tmp_path):
    wav = fetch("https://youtu.be/abc", tmp_path, ydl_cls=FakeYDL)
    assert wav.suffix == ".wav" and wav.exists()
    opts = FakeYDL.last_opts
    assert opts["noplaylist"] is True
    assert opts["postprocessors"][0]["preferredcodec"] == "wav"
    assert opts["postprocessor_args"]["extractaudio"] == ["-ar", "44100"]


def test_cli_fetch_without_yt_dlp_gives_friendly_error(tmp_path, capsys, monkeypatch):
    import builtins

    real = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "yt_dlp":
            raise ImportError
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert main(["fetch", "https://youtu.be/abc", "-d", str(tmp_path)]) == 2
    assert "extra" in capsys.readouterr().err
