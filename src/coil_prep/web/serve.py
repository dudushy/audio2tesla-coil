"""Servidor do app com HTTP e HTTPS na MESMA porta (o microfone exige HTTPS fora do localhost).

Dois uvicorn internos (um sem e um com TLS) compartilham o mesmo app; na porta pública, o primeiro
byte da conexão decide o destino (0x16 = início de TLS). Quem abre uma página por HTTP usando o IP
da rede é levado ao HTTPS; `localhost` já é endereço seguro e fica como está. O certificado é
autoassinado, gerado na 1ª execução em <dados>/tls (o navegador pede para confirmar uma vez).
"""

import asyncio
import ipaddress
import os
import re
import shutil
import subprocess
from pathlib import Path

import uvicorn

from .app import create_app

TLS_HANDSHAKE = b"\x16"
CERT_DAYS = 825
RENEW_BEFORE = 30 * 24 * 3600  # s
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
FIRST_BYTE_TIMEOUT = 30.0


def alt_names(lan_ip: str = "") -> str:
    names = ["DNS:localhost", "IP:127.0.0.1"]
    try:
        ip = ipaddress.ip_address(lan_ip.strip())
    except ValueError:
        return ",".join(names)  # sem IP (ou texto inválido): o navegador só avisa do mesmo jeito
    if str(ip) != "127.0.0.1":
        names.append(f"IP:{ip}")
    return ",".join(names)


def ensure_certificate(folder: Path, lan_ip: str = "") -> tuple[Path, Path]:
    """(certificado, chave). Reaproveita o existente: trocar faria o navegador avisar de novo.

    Gera outro só se faltar, estiver perto de vencer ou o IP da rede informado não constar nele.
    """
    cert, key, names_file = folder / "cert.pem", folder / "key.pem", folder / "names.txt"
    names = alt_names(lan_ip)
    openssl = shutil.which("openssl")
    if openssl is None:
        raise RuntimeError("openssl não encontrado")
    if cert.is_file() and key.is_file() and names_file.is_file():
        known = set(names_file.read_text(encoding="utf-8").split(","))
        fresh = subprocess.run(
            [openssl, "x509", "-in", str(cert), "-noout", "-checkend", str(RENEW_BEFORE)],
            capture_output=True,
            check=False,
        )
        if set(names.split(",")) <= known and fresh.returncode == 0:
            return cert, key
    folder.mkdir(parents=True, exist_ok=True)
    key.unlink(missing_ok=True)
    old_mask = os.umask(0o077)  # a chave privada só para o dono
    try:
        subprocess.run(
            [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
             "-keyout", str(key), "-out", str(cert), "-days", str(CERT_DAYS),
             "-subj", "/CN=coil-prep", "-addext", f"subjectAltName={names}",
             "-addext", "extendedKeyUsage=serverAuth"],
            capture_output=True,
            check=True,
        )  # fmt: skip
    finally:
        os.umask(old_mask)
    names_file.write_text(names, encoding="utf-8")
    return cert, key


def redirect_to_https(app):
    """ASGI: página pedida por HTTP pelo IP/nome da rede -> mesma URL em HTTPS (a API não muda)."""

    async def wrapper(scope, receive, send):
        if scope["type"] == "http" and scope["scheme"] == "http" and scope["method"] == "GET":
            headers = dict(scope["headers"])
            host = headers.get(b"host", b"").decode("latin-1")
            name = re.sub(r":\d+$", "", host).lower()
            if (
                re.fullmatch(r"[\w.\-]+(:\d+)?|\[[0-9a-fA-F:]+\](:\d+)?", host)
                and name not in LOCAL_HOSTS
                and b"text/html" in headers.get(b"accept", b"")
            ):
                query = scope.get("query_string", b"")
                path = scope.get("raw_path") or scope["path"].encode()
                url = b"https://" + host.encode() + path + (b"?" + query if query else b"")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 307,
                        "headers": [(b"location", url), (b"content-length", b"0")],
                    }
                )
                await send({"type": "http.response.body", "body": b""})
                return
        await app(scope, receive, send)

    return wrapper


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
    except OSError:
        writer.close()


def front(plain_port: int, tls_port: int):
    """Atendente da porta pública: encaminha para o uvicorn certo conforme o 1º byte."""

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream = None
        try:
            first = await asyncio.wait_for(reader.read(1), FIRST_BYTE_TIMEOUT)
            if not first:
                return
            port = tls_port if first == TLS_HANDSHAKE else plain_port
            up_reader, upstream = await asyncio.open_connection("127.0.0.1", port)
            upstream.write(first)
            back = asyncio.create_task(_pipe(up_reader, writer))
            forth = asyncio.create_task(_pipe(reader, upstream))
            await back  # o servidor encerrou a resposta/conexão
            forth.cancel()
        except (OSError, TimeoutError, asyncio.TimeoutError):
            pass
        finally:
            for w in (upstream, writer):
                if w is not None:
                    w.close()

    return handle


async def serve(data_dir: Path, host: str, port: int, lan_ip: str = "", https: bool = True) -> None:
    app = create_app(data_dir)
    if https:
        try:
            cert, key = ensure_certificate(data_dir / "tls", lan_ip)
        except (OSError, RuntimeError, subprocess.CalledProcessError) as e:
            print(f"HTTPS desativado (não consegui gerar o certificado: {e})", flush=True)
            https = False
    if not https:
        await uvicorn.Server(uvicorn.Config(app, host=host, port=port)).serve()
        return
    plain_port, tls_port = port + 1, port + 2  # internas, só em 127.0.0.1
    shared = redirect_to_https(app)  # o mesmo app nos dois: a fila de jobs é uma só
    servers = [
        uvicorn.Server(uvicorn.Config(shared, host="127.0.0.1", port=plain_port, lifespan="off")),
        uvicorn.Server(
            uvicorn.Config(
                shared,
                host="127.0.0.1",
                port=tls_port,
                lifespan="off",
                ssl_certfile=str(cert),
                ssl_keyfile=str(key),
            )
        ),
    ]
    public = await asyncio.start_server(front(plain_port, tls_port), host, port)
    print(f"coil-prep na porta {port}: HTTP e HTTPS", flush=True)
    tasks = [asyncio.create_task(s.serve()) for s in servers]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)  # sinal ou falha
    finally:
        public.close()
        for s in servers:
            s.should_exit = True
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    asyncio.run(
        serve(
            Path(os.environ.get("COIL_DATA", "data")),
            os.environ.get("COIL_HOST", "0.0.0.0"),
            int(os.environ.get("COIL_PORT", "1337")),
            os.environ.get("COIL_LAN_IP", ""),
            os.environ.get("COIL_HTTPS", "1") != "0",
        )
    )


if __name__ == "__main__":
    main()
