"""Servidor HTTP local (só biblioteca padrão).

Segurança: escuta apenas em 127.0.0.1, exige em toda chamada da API um token sorteado
a cada execução e confere o cabeçalho Host. Assim um site aberto em outra aba não consegue
comandar o Faxina (nem por CSRF, nem por DNS rebinding).
"""
from __future__ import annotations

import json
import mimetypes
import secrets
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import __version__, config
from .state import ApiError, AppState

# Tipos fixos: no Windows o registro às vezes diz que .js é text/plain, e o navegador bloqueia o script.
MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".json": "application/json; charset=utf-8"}


def _int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _ids(value) -> list[int]:
    if not isinstance(value, list):
        return []
    return [_int(v, -1) for v in value if _int(v, -1) >= 0]


ROUTES = {
    ("GET", "/api/status"): lambda s, q, b: s.status(),
    ("GET", "/api/places"): lambda s, q, b: s.places(),
    ("GET", "/api/browse"): lambda s, q, b: s.browse(q.get("path", "")),
    ("POST", "/api/scan"): lambda s, q, b: s.start_scan(b.get("path", "")),
    ("POST", "/api/scan/cancel"): lambda s, q, b: s.cancel_scan(),
    ("GET", "/api/overview"): lambda s, q, b: s.overview(),
    ("GET", "/api/tree"): lambda s, q, b: s.tree(_int(q.get("id"), 0)),
    ("GET", "/api/files"): lambda s, q, b: s.files(q.get("cat") or None, _int(q.get("older"), 0), _int(q.get("limit"), 200)),
    ("GET", "/api/cleanup"): lambda s, q, b: s.cleanup(),
    ("POST", "/api/duplicates"): lambda s, q, b: s.start_duplicates(_int(b.get("minSize"), 1024 * 1024)),
    ("GET", "/api/duplicates"): lambda s, q, b: s.duplicates(),
    ("POST", "/api/duplicates/cancel"): lambda s, q, b: s.cancel_duplicates(),
    ("POST", "/api/duplicates/reset"): lambda s, q, b: s.reset_duplicates(),
    ("GET", "/api/quarantine"): lambda s, q, b: s.quarantine_list(),
    ("POST", "/api/quarantine/move"): lambda s, q, b: s.quarantine_move(_ids(b.get("files")), _ids(b.get("dirs"))),
    ("POST", "/api/quarantine/restore"): lambda s, q, b: s.quarantine_restore(_ids(b.get("ids"))),
    ("POST", "/api/quarantine/purge"): lambda s, q, b: s.quarantine_purge(None if b.get("all") else _ids(b.get("ids"))),
    ("GET", "/api/history"): lambda s, q, b: {"items": s.history.list(q.get("root") or None)},
    ("POST", "/api/reveal"): lambda s, q, b: s.reveal(str(b.get("path", ""))),
}


class FaxinaServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, state: AppState):
        super().__init__(address, Handler)
        self.state = state
        self.token = secrets.token_urlsafe(24)
        self.web = config.web_dir()

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"


class Handler(BaseHTTPRequestHandler):
    server: FaxinaServer
    server_version = f"Faxina/{__version__}"

    def log_message(self, fmt, *args):  # console limpo
        pass

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def _route(self, method: str) -> None:
        url = urlparse(self.path)
        host = (self.headers.get("Host") or "").lower()
        if host not in (f"127.0.0.1:{self.server.port}", f"localhost:{self.server.port}"):
            return self._json({"error": "Host não permitido."}, 403)
        if method == "GET" and url.path in ("/", "/index.html"):
            return self._index()
        if method == "GET" and url.path.startswith("/static/"):
            return self._static(url.path[len("/static/"):])
        if not url.path.startswith("/api/"):
            return self._json({"error": "Não encontrado."}, 404)
        if not secrets.compare_digest(self.headers.get("X-Faxina-Token", ""), self.server.token):
            return self._json({"error": "Token inválido. Recarregue a página."}, 403)
        if method == "POST" and url.path == "/api/shutdown":
            self._json({"ok": True})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        handler = ROUTES.get((method, url.path))
        if not handler:
            return self._json({"error": "Rota desconhecida."}, 404)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        body = {}
        if method == "POST":
            length = _int(self.headers.get("Content-Length"), 0)
            if length > 2_000_000:
                return self._json({"error": "Requisição grande demais."}, 413)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._json({"error": "JSON inválido."}, 400)
        try:
            self._json(handler(self.server.state, query, body))
        except ApiError as exc:
            self._json({"error": str(exc)}, exc.status)
        except Exception as exc:
            traceback.print_exc()
            self._json({"error": f"Erro interno: {type(exc).__name__}: {exc}"}, 500)

    def _index(self) -> None:
        html = (self.server.web / "index.html").read_text(encoding="utf-8")
        html = html.replace("__FAXINA_TOKEN__", self.server.token).replace("__FAXINA_VERSION__", __version__)
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _static(self, rel: str) -> None:
        base = self.server.web.resolve()
        target = (base / rel).resolve()
        if base not in target.parents or not target.is_file():
            return self._json({"error": "Não encontrado."}, 404)
        ctype = MIME.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send(self, status: int, payload: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass


def make_server(state: AppState, port: int) -> FaxinaServer:
    try:
        return FaxinaServer(("127.0.0.1", port), state)
    except OSError:
        return FaxinaServer(("127.0.0.1", 0), state)  # porta ocupada: o sistema escolhe outra
