import io
import zipfile

import pytest

from coil_prep import scores

# recorte do HTML real da busca do Mutopia (células na mesma ordem)
PAGE = """
<table class="outer-table"><tr><td>
<table class="table-bordered result-table">
<tr><td>F&uuml;r Elise</td><td>by L. V. Beethoven (1770&ndash;1827)</td><td>WoO 59</td><td></td></tr>
<tr><td>for Piano</td><td>1810</td><td>Classical</td><td></td></tr>
<tr><td>Breitkopf</td><td><a href="../legal.html#publicdomain">Public Domain</a></td>
<td><a href="piece-info.cgi?id=931">More Information</a></td><td>2014/01/01</td></tr>
<tr><td>Download: <a href="https://www.mutopiaproject.org/ftp/B/elise/elise.ly">.ly file</a></td>
<td><a href="https://www.mutopiaproject.org/ftp/B/elise/elise.mid">.mid file</a></td></tr>
</table>
<table class="table-bordered result-table">
<tr><td>Sem MIDI</td></tr></table>
</td></tr></table>
<a href="make-table.cgi?startat=10&searchingfor=x">Next 10</a>
"""


def test_parse_search_reads_pieces_with_midi():
    out = scores.parse_search(PAGE)
    assert out["has_next"]
    assert out["results"] == [
        {
            "title": "Für Elise",
            "composer": "L. V. Beethoven (1770–1827)",
            "opus": "WoO 59",
            "instrument": "Piano",
            "license": "Public Domain",
            "url": "https://www.mutopiaproject.org/cgibin/piece-info.cgi?id=931",
            "file": "B/elise/elise.mid",
        }
    ]


@pytest.mark.parametrize(
    "bad",
    [
        "http://evil.example/a.mid",
        "../../etc/passwd.mid",
        "A/../b.mid",
        "A/b.ly",
        "/A/b.mid",
        "A/b.mid?x=1",
        "A/b.zip",
    ],
)
def test_only_midi_paths_inside_ftp_are_downloaded(monkeypatch, bad):
    monkeypatch.setattr(scores, "_download", lambda *a: pytest.fail("não deveria baixar"))
    with pytest.raises(ValueError):
        scores.midi_bytes(bad)


def test_featured_paths_are_valid():
    assert all(scores._FILE.match(p["file"]) for p in scores.FEATURED)


def test_zip_lists_movements_in_order_and_extracts_one(monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name in ("p-10.mid", "p-2.mid", "leia.txt"):
            z.writestr(name, b"MThd" + name.encode())
    monkeypatch.setattr(scores, "_download", lambda *a: buf.getvalue())
    scores._files.clear()
    assert scores.movements("A/p-mids.zip") == ["p-2.mid", "p-10.mid"]
    assert scores.midi_bytes("A/p-mids.zip", "p-10.mid") == b"MThdp-10.mid"
    with pytest.raises(ValueError, match="movimentos"):
        scores.midi_bytes("A/p-mids.zip")
    with pytest.raises(ValueError):
        scores.midi_bytes("A/p-mids.zip", "leia.txt")
    scores._files.clear()
