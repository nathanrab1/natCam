"""Escritor de G-code no dialeto GRBL (G0/G1 apenas, sem ciclos fixos, sem M6)."""

from __future__ import annotations

import math
from datetime import datetime

from .config import Config
from .toolpaths import Hole, Path2D, Toolpaths, circle_path, split_path_for_tabs, tab_intervals

from . import __version__


def _f(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


class GrblWriter:
    def __init__(self, cfg: Config, title: str):
        self.cfg = cfg
        self.lines: list[str] = []
        self._x = self._y = self._z = None
        self._feed = None
        self.header(title)

    # -- blocos básicos ------------------------------------------------------

    def comment(self, text: str):
        self.lines.append(f"({text})")

    def header(self, title: str):
        c = self.cfg
        self.comment(f"{title} - gerado por gcodegen {__version__} em {datetime.now():%Y-%m-%d %H:%M}")
        self.lines += ["G21", "G90", "G94", "G17"]
        self.lines.append(f"G0 Z{_f(c.safe_z)}")
        self._z = c.safe_z
        self.lines.append(f"M3 S{c.spindle}")
        if c.dwell > 0:
            self.lines.append(f"G4 P{_f(c.dwell)}")

    def footer(self):
        self.lines.append(f"G0 Z{_f(self.cfg.safe_z)}")
        self.lines.append("M5")
        self.lines.append("G0 X0 Y0")
        self.lines.append("M2")

    def rapid(self, x=None, y=None, z=None):
        parts = []
        if x is not None and x != self._x:
            parts.append(f"X{_f(x)}")
            self._x = x
        if y is not None and y != self._y:
            parts.append(f"Y{_f(y)}")
            self._y = y
        if z is not None and z != self._z:
            parts.append(f"Z{_f(z)}")
            self._z = z
        if parts:
            self.lines.append("G0 " + " ".join(parts))

    def feed(self, x=None, y=None, z=None, f=None):
        parts = []
        if x is not None and x != self._x:
            parts.append(f"X{_f(x)}")
            self._x = x
        if y is not None and y != self._y:
            parts.append(f"Y{_f(y)}")
            self._y = y
        if z is not None and z != self._z:
            parts.append(f"Z{_f(z)}")
            self._z = z
        if not parts:
            return
        if f is not None and f != self._feed:
            parts.append(f"F{_f(f)}")
            self._feed = f
        self.lines.append("G1 " + " ".join(parts))

    def retract(self):
        self.rapid(z=self.cfg.safe_z)

    # -- operações -----------------------------------------------------------

    def trace_path(self, path: Path2D, depth: float, feed: float):
        self.retract()
        self.rapid(x=path[0][0], y=path[0][1])
        self.feed(z=depth, f=self.cfg.plunge_feed)
        for x, y in path[1:]:
            self.feed(x=x, y=y, f=feed)
        self.retract()

    def drill_hole(self, h: Hole, depth: float):
        c = self.cfg
        self.rapid(x=h.x, y=h.y)
        z = 0.0
        step = c.drill_step if c.drill_step > 0 else abs(depth)
        while z > depth + 1e-9:
            z = max(depth, z - step)
            self.feed(z=z, f=c.drill_feed)
        self.retract()

    def mill_hole(self, h: Hole, tool_dia: float, depth: float, feed: float):
        r = (h.diameter - tool_dia) / 2.0
        if r <= 0:
            self.drill_hole(h, depth)
            return
        ring = circle_path(h.x, h.y, r)
        self.rapid(x=h.x, y=h.y)
        self.feed(z=0.0, f=self.cfg.plunge_feed)
        z = 0.0
        step = self.cfg.drill_step if self.cfg.drill_step > 0 else abs(depth)
        while z > depth + 1e-9:
            z = max(depth, z - step)
            self.feed(x=ring[0][0], y=ring[0][1], f=feed)
            self.feed(z=z, f=self.cfg.plunge_feed)
            for x, y in ring[1:]:
                self.feed(x=x, y=y, f=feed)
            self.feed(x=h.x, y=h.y, f=feed)
        self.retract()

    def cut_with_tabs(self, path: Path2D, depth: float, step: float, feed: float,
                      n_tabs: int, tab_width: float, tab_height: float):
        tab_z = depth + tab_height
        intervals = tab_intervals(path, n_tabs, tab_width) if tab_height > 0 else []
        pieces = split_path_for_tabs(path, intervals)
        self.retract()
        self.rapid(x=path[0][0], y=path[0][1])
        z = 0.0
        while z > depth + 1e-9:
            z = max(depth, z - step)
            self.feed(z=z, f=self.cfg.plunge_feed)
            for pts, is_tab in pieces:
                if is_tab and z < tab_z:
                    self.feed(z=tab_z, f=self.cfg.plunge_feed)
                    for x, y in pts[1:]:
                        self.feed(x=x, y=y, f=feed)
                    self.feed(z=z, f=self.cfg.plunge_feed)
                else:
                    for x, y in pts[1:]:
                        self.feed(x=x, y=y, f=feed)
        self.retract()

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


# ---------------------------------------------------------------------------
# Programas por operação
# ---------------------------------------------------------------------------


def isolation_program(tp: Toolpaths, cfg: Config) -> str:
    w = GrblWriter(cfg, f"Isolacao {cfg.layer} - fresa {cfg.tool_dia}mm, Z{cfg.iso_depth}")
    w.comment(f"{len(tp.isolation)} caminhos, {cfg.iso_passes} passe(s)")
    for path in tp.isolation:
        w.trace_path(path, cfg.iso_depth, cfg.iso_feed)
    w.footer()
    return w.text()


def drill_program(holes: list[Hole], diameter: float, cfg: Config) -> str:
    step = f", passos de {cfg.drill_step}mm" if cfg.drill_step > 0 else ""
    w = GrblWriter(cfg, f"Furacao broca {diameter}mm - {len(holes)} furos, Z{cfg.drill_depth}{step}")
    for h in holes:
        w.drill_hole(h, cfg.drill_depth)
    w.footer()
    return w.text()


def milled_holes_program(holes: list[Hole], cfg: Config) -> str:
    tool = cfg.drill_single_tool or cfg.tool_dia
    w = GrblWriter(cfg, f"Furos fresados com fresa {tool}mm - {len(holes)} furos")
    for h in holes:
        w.mill_hole(h, tool, cfg.drill_depth, cfg.cut_feed)
    w.footer()
    return w.text()


def cutout_program(tp: Toolpaths, cfg: Config) -> str:
    w = GrblWriter(cfg, f"Recorte contorno - fresa {cfg.cut_tool_dia}mm, Z{cfg.cut_depth} em passos de {cfg.cut_step}")
    for path in tp.cutout_inner:
        w.cut_with_tabs(path, cfg.cut_depth, cfg.cut_step, cfg.cut_feed, 0, 0, 0)
    for path in tp.cutout:
        w.cut_with_tabs(path, cfg.cut_depth, cfg.cut_step, cfg.cut_feed,
                        cfg.tabs, cfg.tab_width, cfg.tab_height)
    w.footer()
    return w.text()


def estimate_time(text: str, rapid: float = 1000.0) -> float:
    """Estimativa grosseira de duração (minutos) a partir do G-code."""
    x = y = z = 0.0
    feed = 100.0
    total = 0.0
    for line in text.splitlines():
        if not line.startswith(("G0", "G1")):
            if line.startswith("G4"):
                total += float(line[3:].lstrip("P")) / 60.0
            continue
        parts = line.split()
        nx, ny, nz = x, y, z
        for p in parts[1:]:
            if p[0] == "X":
                nx = float(p[1:])
            elif p[0] == "Y":
                ny = float(p[1:])
            elif p[0] == "Z":
                nz = float(p[1:])
            elif p[0] == "F":
                feed = float(p[1:])
        d = math.sqrt((nx - x) ** 2 + (ny - y) ** 2 + (nz - z) ** 2)
        total += d / (rapid if parts[0] == "G0" else feed)
        x, y, z = nx, ny, nz
    return total
