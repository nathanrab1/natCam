"""Serialização placa/parâmetros -> JSON, compartilhada pelo servidor local e pelo navegador (Pyodide)."""

from __future__ import annotations

from shapely.geometry import MultiPolygon, Polygon

from . import __version__
from .config import PARAM_GROUPS, Config
from .toolpaths import Board, Toolpaths

OPS = ("isolation", "drill", "cutout")


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


def parse_ops(raw) -> tuple[str, ...]:
    return tuple(o for o in (raw or OPS) if o in OPS) or OPS
