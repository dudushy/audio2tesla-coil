import json
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from coil_prep.web.serve import alt_names, ensure_certificate

pytestmark = pytest.mark.skipif(shutil.which("openssl") is None, reason="sem openssl")


def test_certificate_is_reused_until_the_lan_ip_changes(tmp_path):
    cert, key = ensure_certificate(tmp_path, "192.168.0.10")
    first = cert.read_bytes()
    assert key.stat().st_mode & 0o077 == 0
    assert ensure_certificate(tmp_path, "192.168.0.10")[0].read_bytes() == first
    assert ensure_certificate(tmp_path, "")[0].read_bytes() == first  # sem IP: mantém
    assert ensure_certificate(tmp_path, "192.168.0.11")[0].read_bytes() != first


@pytest.mark.parametrize("bad", ["", "abc", "1.2.3.4,DNS:evil.com"])
def test_invalid_lan_ip_is_ignored(bad):
    assert alt_names(bad) == "DNS:localhost,IP:127.0.0.1"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def free_port_block() -> int:
    """Porta p com p, p+1 e p+2 livres."""
    while True:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        try:
            for p in (port + 1, port + 2):
                with socket.socket() as s:
                    s.bind(("127.0.0.1", p))
            return port
        except OSError:
            continue


def test_same_port_serves_http_and_https_and_redirects_lan_pages(tmp_path):
    port = free_port_block()
    env = {
        "COIL_DATA": str(tmp_path),
        "COIL_HOST": "127.0.0.1",
        "COIL_PORT": str(port),
        "PATH": str(__import__("os").environ["PATH"]),
    }
    proc = subprocess.Popen([sys.executable, "-m", "coil_prep.web.serve"], env=env)
    ctx = ssl.create_default_context()
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    opener = urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=ctx))

    def get(scheme, path, host=None, accept="text/html"):
        headers = {"Accept": accept} | ({"Host": host} if host else {})
        req = urllib.request.Request(f"{scheme}://127.0.0.1:{port}{path}", headers=headers)
        try:
            with opener.open(req, timeout=5) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, b""

    try:
        for _ in range(100):
            try:
                get("https", "/api/state")
                break
            except OSError:
                time.sleep(0.2)
        for scheme in ("http", "https"):
            status, _, body = get(scheme, "/api/state", accept="*/*")
            assert status == 200 and "peak_db" in json.loads(body), scheme
        assert get("http", "/")[0] == 200  # localhost/127.0.0.1 já é seguro
        lan = f"192.168.0.10:{port}"
        status, headers, _ = get("http", "/soundpad?x=1", host=lan)
        assert status == 307 and headers["location"] == f"https://{lan}/soundpad?x=1"
        assert get("https", "/soundpad", host=lan)[0] == 200
        assert get("http", "/api/state", host=lan, accept="*/*")[0] == 200
    finally:
        proc.terminate()
        assert proc.wait(timeout=15) is not None
