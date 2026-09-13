"""Ponte para rodar no navegador via Pyodide (GitHub Pages, sem servidor).

O JS chama estas funções; tudo entra e sai como JSON. Os arquivos gerados
são gravados no sistema de arquivos virtual e devolvidos como texto/bytes
para virarem downloads na página.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from .api import board_json, params_json, parse_ops
from .config import config_from_dict
from .pipeline import LoadedBoard, generate_toolpaths, load_board, make_board, write_programs

WORK = Path("/tmp/gcodegen")
_state: dict = {"loaded": None, "files": {}}


def params() -> str:
    return json.dumps(params_json())


def load(zip_bytes, filename: str) -> str:
    WORK.mkdir(parents=True, exist_ok=True)
    src = WORK / Path(filename).name
    src.write_bytes(bytes(zip_bytes))
    log: list[str] = []
    loaded = load_board(src, log.append)
    _state["loaded"] = loaded
    _state["files"] = {}
    return json.dumps({
        "name": loaded.name, "layers": sorted(loaded.copper),
        "has_outline": loaded.outline is not None, "log": log,
    })


def _cfg(config_json: str):
    loaded: LoadedBoard | None = _state["loaded"]
    if loaded is None:
        raise ValueError("abra um ZIP primeiro")
    cfg = config_from_dict(json.loads(config_json or "{}"))
    if not loaded.has_layer(cfg.layer):
        raise ValueError(f"o ZIP não tem a camada {cfg.layer}")
    return loaded, cfg


def preview(config_json: str) -> str:
    loaded, cfg = _cfg(config_json)
    return json.dumps({"board": board_json(make_board(loaded, cfg), None)})


def generate(config_json: str, ops_json: str) -> str:
    loaded, cfg = _cfg(config_json)
    ops = parse_ops(json.loads(ops_json or "null"))
    board = make_board(loaded, cfg)
    tp = generate_toolpaths(board, cfg)
    out = WORK / "out" / loaded.name
    log: list[str] = []
    files = write_programs(loaded.name, board, tp, cfg, out, log.append,
                           isolation="isolation" in ops, drill="drill" in ops,
                           cutout="cutout" in ops, preview=len(ops) == 3)
    for f in files:
        _state["files"][f.name] = f.read_text()
    return json.dumps({
        "board": board_json(board, tp, ops), "log": log,
        "files": [{"name": n, "size": len(t.encode()), "text": t} for n, t in _state["files"].items()],
    })


def all_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in _state["files"].items():
            zf.writestr(name, text)
    return buf.getvalue()
