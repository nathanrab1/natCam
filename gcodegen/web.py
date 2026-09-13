"""Servidor web local: abre a interface no navegador (http://127.0.0.1:8765).

Só biblioteca padrão. Estado em memória por sessão (token); os arquivos
gerados são gravados em <projeto>/output/<nome>/ e servidos para download.
"""

from __future__ import annotations

import argparse
import io
import json
import secrets
import subprocess
import sys
import threading
import traceback
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from shapely.geometry import MultiPolygon, Polygon

from . import __version__
from .config import PARAM_GROUPS, Config, config_from_dict
from .pipeline import LoadedBoard, generate_toolpaths, load_board, make_board, write_programs
from .toolpaths import Board, Toolpaths

STATIC = Path(__file__).parent / "static"
OUTPUT_ROOT = Path(__file__).resolve().parent.parent / "output"

SESSIONS: dict[str, dict] = {}
LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# geometria -> JSON para o canvas
# ---------------------------------------------------------------------------


def _polys(geom) -> list[list[list[list[float]]]]:
    """Lista de polígonos; cada polígono = lista de anéis (externo primeiro)."""
    if geom is None or geom.is_empty:
        return []
    polys = [geom] if isinstance(geom, Polygon) else (
        list(geom.geoms) if isinstance(geom, MultiPolygon) else [])
    out = []
    for p in polys:
        rings = [[[round(x, 4), round(y, 4)] for x, y in p.exterior.coords]]
        rings += [[[round(x, 4), round(y, 4)] for x, y in r.coords] for r in p.interiors]
        out.append(rings)
    return out


OPS = ("isolation", "drill", "cutout")


def board_json(board: Board, tp: Toolpaths | None, ops=OPS) -> dict:
    d = {
        "bounds": [round(v, 4) for v in board.bounds],
        "mirrored": board.mirrored,
        "outline": _polys(board.outline),
        "copper": _polys(board.copper),
        "holes": [[round(h.x, 4), round(h.y, 4), h.diameter] for h in board.holes],
        "isolation": [], "cutout": [], "milled": [],
    }
    if tp:
        if "isolation" in ops:
            d["isolation"] = [[[round(x, 4), round(y, 4)] for x, y in p] for p in tp.isolation]
        if "cutout" in ops:
            d["cutout"] = [[[round(x, 4), round(y, 4)] for x, y in p] for p in tp.cutout + tp.cutout_inner]
        if "drill" in ops:
            d["milled"] = [[round(h.x, 4), round(h.y, 4), h.diameter] for h in tp.milled_holes]
    return d


def params_json() -> dict:
    defaults = Config()
    groups = []
    for name, items in PARAM_GROUPS.items():
        fields = []
        for attr, label, kind, help_text in items:
            value = getattr(defaults, attr)
            if attr == "mirror":
                value = "auto"
            if kind is bool:
                k = "bool"
            elif kind is int:
                k = "int"
            elif kind is float:
                k = "float"
            elif kind == "optfloat":
                k = "optfloat"
                value = "" if value is None else value
            else:
                k = "choice"
            f = {"name": attr, "label": label, "kind": k, "value": value, "help": help_text}
            if k == "choice":
                f["options"] = kind[7:].split(",")
            fields.append(f)
        groups.append({"name": name, "fields": fields})
    return {"version": __version__, "groups": groups}


def resolve_out_dir(raw: str | None, name: str) -> Path:
    """Pasta de destino: a informada pelo usuário ou output/<nome> no projeto."""
    if not raw or not str(raw).strip():
        return OUTPUT_ROOT / name
    out = Path(str(raw).strip()).expanduser()
    if not out.is_absolute():
        raise ValueError(f"informe um caminho absoluto para a pasta de destino (ex.: /Users/você/Desktop/gcode): {raw}")
    if out.exists() and not out.is_dir():
        raise ValueError(f"{out} existe e não é uma pasta")
    return out


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = f"gcodegen/{__version__}"

    def log_message(self, fmt, *args):  # silencia o log padrão
        if "--verbose" in sys.argv:
            super().log_message(fmt, *args)

    # -- utilidades ----------------------------------------------------------

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode())

    def _error(self, msg: str, code: int = 400):
        self._json({"error": msg}, code)

    def _body(self) -> bytes:
        n = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(n) if n else b""

    def _session(self, token: str | None) -> dict | None:
        with LOCK:
            return SESSIONS.get(token or "")

    # -- GET -----------------------------------------------------------------

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, (STATIC / "index.html").read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/params":
            self._json(params_json())
        elif path == "/api/initial":
            # sessão pré-carregada por `gcodegen-web placa.zip`
            s = self._session("initial")
            if s:
                loaded: LoadedBoard = s["loaded"]
                self._json({"token": "initial", "name": loaded.name, "layers": sorted(loaded.copper),
                            "has_outline": loaded.outline is not None, "log": s.get("log", [])})
            else:
                self._json({})
        elif path.startswith("/api/files/"):
            self._download(path[len("/api/files/"):])
        elif path == "/api/choose-dir":
            self._choose_dir()
        elif path == "/api/reveal":
            # abre a pasta de saída no Finder (só faz sentido localmente)
            token = urlparse(self.path).query.split("=", 1)[-1]
            s = self._session(token)
            if s and s.get("out"):
                subprocess.Popen(["open", str(s["out"])])
                self._json({"ok": True})
            else:
                self._error("nada gerado ainda", 404)
        else:
            self._send(404, b"not found", "text/plain")

    def _choose_dir(self):
        """Abre o seletor de pastas nativo (macOS) e devolve o caminho escolhido."""
        if sys.platform != "darwin":
            return self._json({"error": "seletor nativo disponível só no macOS; digite o caminho"}, 501)
        script = 'POSIX path of (choose folder with prompt "Pasta de destino dos arquivos G-code")'
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if r.returncode != 0:
            return self._json({"cancelled": True})   # usuário cancelou
        self._json({"path": r.stdout.strip().rstrip("/")})

    def _download(self, rest: str):
        token, _, name = rest.partition("/")
        s = self._session(token)
        if not s or not s.get("files"):
            return self._error("sessão inválida ou nada gerado", 404)
        files: list[Path] = s["files"]
        if name == "all.zip":
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in files:
                    zf.write(f, f.name)
            self._send(200, buf.getvalue(), "application/zip",
                       {"Content-Disposition": f'attachment; filename="{s["name"]}_gcode.zip"'})
            return
        for f in files:
            if f.name == name:
                ctype = "image/svg+xml" if f.suffix == ".svg" else "text/plain; charset=utf-8"
                disp = "inline" if f.suffix == ".svg" else "attachment"
                self._send(200, f.read_bytes(), ctype, {"Content-Disposition": f'{disp}; filename="{f.name}"'})
                return
        self._error("arquivo não encontrado", 404)

    # -- POST ----------------------------------------------------------------

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/load":
                self._load()
            elif path == "/api/preview":
                self._preview()
            elif path == "/api/generate":
                self._generate()
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as e:  # erro de parse/geometria vira mensagem na página
            traceback.print_exc()
            self._error("".join(traceback.format_exception_only(type(e), e)).strip(), 500)

    def _load(self):
        filename = self.headers.get("X-Filename", "placa.zip")
        data = self._body()
        if not data:
            return self._error("arquivo vazio")
        log: list[str] = []
        tmp = OUTPUT_ROOT / "_upload"
        tmp.mkdir(parents=True, exist_ok=True)
        src = tmp / Path(filename).name
        src.write_bytes(data)
        loaded = load_board(src, log.append)
        token = secrets.token_urlsafe(12)
        with LOCK:
            SESSIONS[token] = {"loaded": loaded, "name": loaded.name, "files": [], "out": None}
        self._json({
            "token": token, "name": loaded.name, "layers": sorted(loaded.copper),
            "has_outline": loaded.outline is not None,
            "drills": [t.diameter for df in loaded.drills for t in df.tools], "log": log,
        })

    def _read_request(self):
        req = json.loads(self._body() or b"{}")
        self._req = req
        s = self._session(req.get("token"))
        if not s:
            raise ValueError("sessão expirada: abra o ZIP de novo")
        cfg = config_from_dict(req.get("config", {}))
        loaded: LoadedBoard = s["loaded"]
        if not loaded.has_layer(cfg.layer):
            raise ValueError(f"o ZIP não tem a camada {cfg.layer}")
        return s, loaded, cfg

    def _preview(self):
        s, loaded, cfg = self._read_request()
        board = make_board(loaded, cfg)
        self._json({"board": board_json(board, None)})

    def _generate(self):
        s, loaded, cfg = self._read_request()
        req = self._req
        ops = tuple(o for o in req.get("ops", OPS) if o in OPS) or OPS
        board = make_board(loaded, cfg)
        tp = generate_toolpaths(board, cfg)
        out = resolve_out_dir(req.get("out_dir"), loaded.name)
        log: list[str] = [f"Saída: {out}"]
        files = write_programs(loaded.name, board, tp, cfg, out, log.append,
                               isolation="isolation" in ops, drill="drill" in ops,
                               cutout="cutout" in ops, preview=len(ops) == len(OPS))
        with LOCK:
            # acumula o que já foi gerado nesta sessão (uma aba por vez)
            if s.get("out") != out:
                s["files"] = []
            known = {f.name: f for f in s["files"]}
            known.update({f.name: f for f in files})
            s["files"] = list(known.values())
            s["out"] = out
            all_files = s["files"]
        self._json({
            "board": board_json(board, tp, ops), "log": log, "out": str(out),
            "files": [{"name": f.name, "size": f.stat().st_size} for f in all_files],
        })


# ---------------------------------------------------------------------------


def open_browser(url: str):
    try:
        if sys.platform == "darwin":
            r = subprocess.run(["open", "-a", "Google Chrome", url], capture_output=True)
            if r.returncode == 0:
                return
    except Exception:
        pass
    webbrowser.open(url)


def main(argv=None):
    p = argparse.ArgumentParser(prog="gcodegen-web", description="Interface web local do gerador de G-code")
    p.add_argument("zip", nargs="?", help="ZIP do KiCad para abrir já ao iniciar")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true", help="não abre o navegador automaticamente")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)

    if args.zip:
        log: list[str] = []
        loaded = load_board(args.zip, log.append)
        SESSIONS["initial"] = {"loaded": loaded, "name": loaded.name, "files": [], "out": None, "log": log}

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Gerador de G-code {__version__} rodando em {url}  (Ctrl+C para encerrar)")
    if not args.no_browser:
        threading.Timer(0.5, open_browser, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nencerrado")


if __name__ == "__main__":
    main()
