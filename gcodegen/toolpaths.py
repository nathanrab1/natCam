"""Geração dos caminhos de ferramenta a partir da geometria da placa."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shapely import affinity
from shapely.geometry import LineString, LinearRing, MultiPolygon, Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import substring, unary_union

from .config import Config
from .excellon import DrillFile, DrillTool

Path2D = list[tuple[float, float]]


@dataclass
class Hole:
    x: float
    y: float
    diameter: float


@dataclass
class Board:
    """Geometria já transformada (espelhada/transladada) para a máquina."""

    copper: BaseGeometry
    outline: Polygon | None
    holes: list[Hole]
    slots: list[tuple[tuple[float, float], tuple[float, float], float]]
    bounds: tuple[float, float, float, float]
    mirrored: bool


@dataclass
class Toolpaths:
    isolation: list[Path2D] = field(default_factory=list)
    drills: dict[float, list[Hole]] = field(default_factory=dict)      # diâmetro -> furos
    milled_holes: list[Hole] = field(default_factory=list)
    cutout: list[Path2D] = field(default_factory=list)
    cutout_inner: list[Path2D] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Montagem da placa e transformação para coordenadas de máquina
# ---------------------------------------------------------------------------


def build_board(copper: BaseGeometry, outline: Polygon | None,
                drill_files: list[DrillFile], cfg: Config) -> Board:
    holes = [Hole(x, y, t.diameter) for df in drill_files for t in df.tools for (x, y) in t.holes]
    slots = [(a, b, t.diameter) for df in drill_files for t in df.tools for (a, b) in t.slots]

    ref = outline if outline is not None and not outline.is_empty else copper
    if ref.is_empty:
        raise ValueError("placa sem geometria (nem cobre nem contorno)")
    minx, miny, maxx, maxy = ref.bounds

    mirror = cfg.mirror if cfg.mirror is not None else (cfg.layer == "B_Cu")

    def xf(g: BaseGeometry) -> BaseGeometry:
        if mirror:
            g = affinity.scale(g, xfact=-1, yfact=1, origin=((minx + maxx) / 2, 0))
        if cfg.origin == "board":
            g = affinity.translate(g, xoff=-minx, yoff=-miny)
        return g

    def xf_pt(x: float, y: float) -> tuple[float, float]:
        if mirror:
            x = (minx + maxx) - x
        if cfg.origin == "board":
            x, y = x - minx, y - miny
        return (x, y)

    copper_t = xf(copper)
    outline_t = xf(outline) if outline is not None else None
    holes_t = [Hole(*xf_pt(h.x, h.y), h.diameter) for h in holes]
    slots_t = [(xf_pt(*a), xf_pt(*b), d) for a, b, d in slots]
    ref_t = xf(ref)
    return Board(copper_t, outline_t, holes_t, slots_t, ref_t.bounds, mirror)


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------


def _rings(geom: BaseGeometry) -> list[LinearRing]:
    polys: list[Polygon] = []
    if isinstance(geom, Polygon):
        polys = [geom]
    elif isinstance(geom, MultiPolygon):
        polys = list(geom.geoms)
    elif hasattr(geom, "geoms"):
        for g in geom.geoms:
            polys.extend(_rings_polys(g))
    rings = []
    for p in polys:
        if p.is_empty:
            continue
        rings.append(p.exterior)
        rings.extend(p.interiors)
    return rings


def _rings_polys(g):
    if isinstance(g, Polygon):
        return [g]
    if isinstance(g, MultiPolygon):
        return list(g.geoms)
    return []


def _lines(geom: BaseGeometry) -> list[LineString]:
    if geom.is_empty:
        return []
    if isinstance(geom, (LineString, LinearRing)):
        return [LineString(geom.coords)]
    if hasattr(geom, "geoms"):
        out = []
        for g in geom.geoms:
            out.extend(_lines(g))
        return out
    return []


def _simplify(path: Path2D, tol: float = 0.005) -> Path2D:
    if len(path) < 3:
        return path
    return list(LineString(path).simplify(tol, preserve_topology=False).coords)


def order_paths(paths: list[Path2D], start=(0.0, 0.0)) -> list[Path2D]:
    """Vizinho mais próximo. Caminhos fechados são rotacionados para começar
    no vértice mais próximo da posição atual."""
    remaining = list(paths)
    ordered: list[Path2D] = []
    cx, cy = start
    while remaining:
        best_i, best_d, best_k = 0, math.inf, 0
        for i, p in enumerate(remaining):
            closed = p[0] == p[-1]
            cand = range(len(p) - 1) if closed else (0, len(p) - 1)
            for k in cand:
                d = (p[k][0] - cx) ** 2 + (p[k][1] - cy) ** 2
                if d < best_d:
                    best_i, best_d, best_k = i, d, k
        p = remaining.pop(best_i)
        if p[0] == p[-1]:
            if best_k:
                p = p[best_k:-1] + p[:best_k] + [p[best_k]]
        elif best_k != 0:
            p = p[::-1]
        ordered.append(p)
        cx, cy = p[-1]
    return ordered


def order_holes(holes: list[Hole], start=(0.0, 0.0)) -> list[Hole]:
    remaining = list(holes)
    ordered = []
    cx, cy = start
    while remaining:
        i = min(range(len(remaining)),
                key=lambda k: (remaining[k].x - cx) ** 2 + (remaining[k].y - cy) ** 2)
        h = remaining.pop(i)
        ordered.append(h)
        cx, cy = h.x, h.y
    return ordered


# ---------------------------------------------------------------------------
# Isolação
# ---------------------------------------------------------------------------


def isolation_paths(board: Board, cfg: Config) -> list[Path2D]:
    if board.copper.is_empty:
        return []
    copper = board.copper
    # descarta trechos fora da placa (o recorte remove esse material); deixa
    # a fresa ir um pouco além da borda para isolar cobre que encosta nela
    clip = None
    if board.outline is not None:
        clip = board.outline.buffer(cfg.tool_dia, join_style="mitre")
    paths: list[Path2D] = []
    step = cfg.tool_dia * (1.0 - cfg.iso_overlap)
    for k in range(max(1, cfg.iso_passes)):
        off = cfg.tool_dia / 2.0 + k * step
        g = copper.buffer(off, quad_segs=16, join_style="round")
        for ring in _rings(g):
            line = LineString(ring.coords)
            if clip is not None:
                line = line.intersection(clip)
            for seg in _lines(line):
                pts = _simplify(list(seg.coords))
                if len(pts) >= 2:
                    paths.append(pts)
    return order_paths(paths)


# ---------------------------------------------------------------------------
# Furação
# ---------------------------------------------------------------------------


def drill_plan(board: Board, cfg: Config) -> tuple[dict[float, list[Hole]], list[Hole]]:
    holes = list(board.holes)
    # rasgos: aproxima por uma fileira de furos
    for (ax, ay), (bx, by), d in board.slots:
        length = math.hypot(bx - ax, by - ay)
        n = max(1, int(math.ceil(length / (d * 0.5))))
        for i in range(n + 1):
            t = i / n
            holes.append(Hole(ax + (bx - ax) * t, ay + (by - ay) * t, d))

    milled: list[Hole] = []
    groups: dict[float, list[Hole]] = {}
    for h in holes:
        if cfg.drill_single_tool:
            if cfg.mill_holes and h.diameter > cfg.drill_single_tool + 0.05:
                milled.append(h)
            else:
                groups.setdefault(cfg.drill_single_tool, []).append(h)
        else:
            groups.setdefault(h.diameter, []).append(h)
    for d in groups:
        groups[d] = order_holes(groups[d])
    return dict(sorted(groups.items())), order_holes(milled)


def circle_path(cx: float, cy: float, r: float, segments: int = 48) -> Path2D:
    return [(cx + r * math.cos(2 * math.pi * i / segments), cy + r * math.sin(2 * math.pi * i / segments))
            for i in range(segments + 1)]


# ---------------------------------------------------------------------------
# Recorte do contorno
# ---------------------------------------------------------------------------


def cutout_paths(board: Board, cfg: Config) -> tuple[list[Path2D], list[Path2D]]:
    """Retorna (contorno externo, recortes internos), já deslocados pelo
    raio da fresa. Externo passa por fora; internos por dentro do furo."""
    if board.outline is None or board.outline.is_empty:
        return [], []
    r = cfg.cut_tool_dia / 2.0
    outer = Polygon(board.outline.exterior.coords).buffer(r, quad_segs=16, join_style="round")
    outer_paths = [_simplify(list(ring.coords)) for ring in _rings(outer)]
    inner_paths: list[Path2D] = []
    for hole in board.outline.interiors:
        g = Polygon(hole.coords).buffer(-r, quad_segs=16)
        for ring in _rings(g):
            inner_paths.append(_simplify(list(ring.coords)))
    return order_paths(outer_paths), order_paths(inner_paths)


def tab_intervals(path: Path2D, n_tabs: int, tab_width: float) -> list[tuple[float, float]]:
    """Posições (ao longo do comprimento) onde a fresa deve subir para as tabs."""
    if n_tabs <= 0 or tab_width <= 0:
        return []
    length = LineString(path).length
    if length < n_tabs * tab_width * 2:
        return []
    out = []
    for i in range(n_tabs):
        s = length * (i + 0.5) / n_tabs
        out.append((s - tab_width / 2, s + tab_width / 2))
    return out


def split_path_for_tabs(path: Path2D, intervals: list[tuple[float, float]]) -> list[tuple[Path2D, bool]]:
    """Divide o caminho em trechos (pontos, é_tab)."""
    if not intervals:
        return [(path, False)]
    line = LineString(path)
    cuts = [0.0]
    for a, b in intervals:
        cuts.extend([a, b])
    cuts.append(line.length)
    pieces = []
    for i in range(len(cuts) - 1):
        seg = substring(line, cuts[i], cuts[i + 1])
        pts = list(seg.coords)
        if len(pts) < 2:
            continue
        pieces.append((pts, i % 2 == 1))
    return pieces


# ---------------------------------------------------------------------------


def generate_toolpaths(board: Board, cfg: Config) -> Toolpaths:
    tp = Toolpaths()
    tp.isolation = isolation_paths(board, cfg)
    tp.drills, tp.milled_holes = drill_plan(board, cfg)
    tp.cutout, tp.cutout_inner = cutout_paths(board, cfg)
    return tp
