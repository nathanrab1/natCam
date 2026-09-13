"""Parser RS-274X (Gerber) -> geometria shapely.

Cobre o subconjunto que o KiCad emite para camadas de cobre e contorno:
apertures padrão (C, R, O, P), macros (%AM) com variáveis e expressões,
traços G01/G02/G03, regiões G36/G37, polaridade LPD/LPC e flashes D03.
"""

from __future__ import annotations

import ast
import math
import operator
import re
from dataclasses import dataclass, field

from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import linemerge, polygonize, unary_union
from shapely import affinity

ARC_SEGMENTS_PER_MM = 8  # resolução na conversão de arcos em polilinhas
MIN_ARC_SEGMENTS = 8


class GerberError(Exception):
    pass


# ---------------------------------------------------------------------------
# Avaliação de expressões de macro ($1+$1, 2x$3, ...)
# ---------------------------------------------------------------------------

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}


def eval_macro_expr(expr: str, vars_: dict[int, float]) -> float:
    """Avalia uma expressão de macro Gerber. 'x' e 'X' são multiplicação."""
    text = expr.strip().replace("x", "*").replace("X", "*")
    text = re.sub(r"\$(\d+)", lambda m: repr(vars_.get(int(m.group(1)), 0.0)), text)

    def _eval(node):
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            return _BIN_OPS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, ast.UnaryOp):
            v = _eval(node.operand)
            return -v if isinstance(node.op, ast.USub) else v
        raise GerberError(f"expressão de macro não suportada: {expr!r}")

    try:
        return _eval(ast.parse(text, mode="eval"))
    except SyntaxError as e:
        raise GerberError(f"expressão de macro inválida: {expr!r}") from e


# ---------------------------------------------------------------------------
# Apertures
# ---------------------------------------------------------------------------


@dataclass
class Aperture:
    code: int
    shape: str                      # 'C', 'R', 'O', 'P' ou nome de macro
    params: list[float]
    geometry: BaseGeometry          # forma centrada na origem
    is_circle: bool = False         # traços com apertura circular viram buffer

    @property
    def diameter(self) -> float:
        """Largura efetiva para traços (diâmetro do círculo ou menor lado)."""
        if self.shape == "C":
            return self.params[0]
        minx, miny, maxx, maxy = self.geometry.bounds
        return min(maxx - minx, maxy - miny)


def _circle(d: float, cx: float = 0.0, cy: float = 0.0) -> Polygon:
    return Point(cx, cy).buffer(d / 2.0, quad_segs=32)


def _obround(w: float, h: float) -> Polygon:
    if w > h:
        return LineString([(-(w - h) / 2, 0), ((w - h) / 2, 0)]).buffer(h / 2, quad_segs=32)
    return LineString([(0, -(h - w) / 2), (0, (h - w) / 2)]).buffer(w / 2, quad_segs=32)


def _regular_polygon(d: float, n: int, rot: float) -> Polygon:
    r = d / 2.0
    pts = [
        (r * math.cos(math.radians(rot) + 2 * math.pi * i / n),
         r * math.sin(math.radians(rot) + 2 * math.pi * i / n))
        for i in range(int(n))
    ]
    return Polygon(pts)


def _with_hole(geom: BaseGeometry, hole_d: float | None) -> BaseGeometry:
    if hole_d and hole_d > 0:
        return geom.difference(_circle(hole_d))
    return geom


class MacroTemplate:
    """Definição %AM...: lista de primitivas com expressões."""

    def __init__(self, name: str, body: str):
        self.name = name
        self.primitives: list[list[str]] = []
        self.assignments: list[tuple[int, str]] = []
        self.blocks: list[tuple[str, object]] = []  # ordem: ('prim', [...]) | ('set', (n, expr))
        for block in body.split("*"):
            block = block.strip()
            if not block or block.startswith("0 ") or block == "0":
                continue  # comentário de macro
            m = re.match(r"^\$(\d+)\s*=\s*(.+)$", block)
            if m:
                self.blocks.append(("set", (int(m.group(1)), m.group(2))))
                continue
            self.blocks.append(("prim", [p.strip() for p in block.split(",")]))

    def instantiate(self, args: list[float]) -> BaseGeometry:
        vars_ = {i + 1: v for i, v in enumerate(args)}
        dark: list[BaseGeometry] = []
        result: BaseGeometry = Polygon()
        for kind, payload in self.blocks:
            if kind == "set":
                n, expr = payload
                vars_[n] = eval_macro_expr(expr, vars_)
                continue
            fields = payload
            code = int(eval_macro_expr(fields[0], vars_))
            vals = [eval_macro_expr(f, vars_) for f in fields[1:]]
            geom, exposure = self._primitive(code, vals, fields, vars_)
            if geom is None or geom.is_empty:
                continue
            if exposure:
                result = unary_union([result, geom]) if not result.is_empty else geom
            else:
                result = result.difference(geom)
        return result

    @staticmethod
    def _primitive(code: int, v: list[float], raw: list[str], vars_):
        if code == 1:  # círculo: exp, d, cx, cy, [rot]
            exp, d, cx, cy = v[0], v[1], v[2], v[3]
            g = _circle(d, cx, cy)
            if len(v) > 4 and v[4]:
                g = affinity.rotate(g, v[4], origin=(0, 0))
            return g, exp > 0
        if code in (2, 20):  # linha vetorial: exp, w, x1, y1, x2, y2, rot
            exp, w, x1, y1, x2, y2, rot = v[:7]
            g = LineString([(x1, y1), (x2, y2)]).buffer(w / 2, cap_style="flat")
            return affinity.rotate(g, rot, origin=(0, 0)), exp > 0
        if code == 21:  # linha central: exp, w, h, cx, cy, rot
            exp, w, h, cx, cy, rot = v[:6]
            g = box(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
            return affinity.rotate(g, rot, origin=(0, 0)), exp > 0
        if code == 22:  # lower-left line (obsoleto): exp, w, h, x, y, rot
            exp, w, h, x, y, rot = v[:6]
            g = box(x, y, x + w, y + h)
            return affinity.rotate(g, rot, origin=(0, 0)), exp > 0
        if code == 4:  # outline: exp, n, x0, y0, ..., xn, yn, rot
            exp, n = v[0], int(v[1])
            pts = [(v[2 + 2 * i], v[3 + 2 * i]) for i in range(n + 1)]
            rot = v[2 + 2 * (n + 1)] if len(v) > 2 + 2 * (n + 1) else 0.0
            g = Polygon(pts)
            if not g.is_valid:
                g = g.buffer(0)
            return affinity.rotate(g, rot, origin=(0, 0)), exp > 0
        if code == 5:  # polígono regular: exp, n, cx, cy, d, rot
            exp, n, cx, cy, d, rot = v[:6]
            g = affinity.translate(_regular_polygon(d, int(n), rot), cx, cy)
            return g, exp > 0
        if code == 6:  # moiré (obsoleto) -> aproxima pelo círculo externo
            cx, cy, d = v[0], v[1], v[2]
            return _circle(d, cx, cy), True
        if code == 7:  # térmico: cx, cy, d_ext, d_int, gap, rot
            cx, cy, do, di, gap, rot = v[:6]
            ring = _circle(do, cx, cy).difference(_circle(di, cx, cy))
            cross = unary_union([
                box(cx - do, cy - gap / 2, cx + do, cy + gap / 2),
                box(cx - gap / 2, cy - do, cx + gap / 2, cy + do),
            ])
            g = ring.difference(affinity.rotate(cross, rot, origin=(cx, cy)))
            return g, True
        raise GerberError(f"primitiva de macro {code} não suportada")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


@dataclass
class GerberLayer:
    """Resultado do parse: geometria 'dark' unida + centerlines dos traços."""

    geometry: BaseGeometry
    paths: list[LineString] = field(default_factory=list)  # centerlines (útil p/ contorno)
    units: str = "mm"

    def outline_polygon(self) -> Polygon | None:
        """Reconstrói o polígono fechado a partir das centerlines (Edge.Cuts)."""
        if not self.paths:
            return None
        merged = linemerge(unary_union(self.paths))
        polys = list(polygonize(merged))
        if not polys:
            return None
        # polygonize já devolve a face externa com os recortes internos
        # como interiors; a placa é a face de maior área
        polys.sort(key=lambda p: p.area, reverse=True)
        return polys[0]


class GerberParser:
    def __init__(self):
        self.units = "mm"
        self.int_digits = 4
        self.dec_digits = 6
        self.zero_omission = "L"       # L = leading omitido, T = trailing omitido
        self.absolute = True
        self.apertures: dict[int, Aperture] = {}
        self.macros: dict[str, MacroTemplate] = {}
        self.current: Aperture | None = None
        self.polarity_dark = True
        self.interp = "G01"            # G01 linear, G02 CW, G03 CCW
        self.quadrant = "multi"        # G75 (padrão moderno)
        self.x = 0.0
        self.y = 0.0
        self.in_region = False
        self.region_pts: list[tuple[float, float]] = []
        self.dark: list[BaseGeometry] = []
        self.clear_ops: list[tuple[int, BaseGeometry]] = []  # (índice em dark, geom)
        self.paths: list[LineString] = []
        self._scale = 1.0

    # -- API -----------------------------------------------------------------

    def parse(self, text: str) -> GerberLayer:
        # remove comentários G04 e quebras; extended commands ficam entre %...%
        pos = 0
        n = len(text)
        while pos < n:
            ch = text[pos]
            if ch in " \r\n\t":
                pos += 1
                continue
            if ch == "%":
                end = text.find("%", pos + 1)
                if end < 0:
                    raise GerberError("comando estendido sem % de fechamento")
                self._extended(text[pos + 1:end])
                pos = end + 1
                continue
            end = text.find("*", pos)
            if end < 0:
                break
            self._word(text[pos:end].strip())
            pos = end + 1
        return self._finish()

    # -- comandos estendidos (%...%) -----------------------------------------

    def _extended(self, body: str):
        body = body.strip()
        if body.startswith("FS"):
            m = re.match(r"FS([LTD]?)([AI]?)X(\d)(\d)Y(\d)(\d)", body)
            if not m:
                raise GerberError(f"FS inválido: {body}")
            self.zero_omission = m.group(1) or "L"
            self.absolute = (m.group(2) or "A") == "A"
            self.int_digits, self.dec_digits = int(m.group(3)), int(m.group(4))
        elif body.startswith("MO"):
            self.units = "in" if "IN" in body else "mm"
            self._scale = 25.4 if self.units == "in" else 1.0
        elif body.startswith("AM"):
            name, _, rest = body[2:].partition("*")
            self.macros[name.strip()] = MacroTemplate(name.strip(), rest)
        elif body.startswith("ADD"):
            self._define_aperture(body[3:].rstrip("*"))
        elif body.startswith("LP"):
            self.polarity_dark = body[2:3] == "D"
        elif body.startswith(("TF", "TA", "TO", "TD")):
            pass  # atributos X2 — irrelevantes para a geometria
        elif body.startswith("LM") or body.startswith("LR") or body.startswith("LS"):
            if body.rstrip("*") not in ("LMN", "LR0", "LS1"):
                raise GerberError(f"transformação de apertura não suportada: {body}")
        elif body.startswith("SR"):
            if body.rstrip("*") != "SR":
                raise GerberError("step-and-repeat (%SR) não suportado")
        elif body.startswith("AB"):
            raise GerberError("block aperture (%AB) não suportado")
        elif body.startswith(("IP", "IN", "LN", "OF", "SF", "AS", "MI", "IR")):
            pass  # comandos obsoletos, KiCad não emite
        else:
            raise GerberError(f"comando estendido desconhecido: %{body}%")

    def _define_aperture(self, spec: str):
        m = re.match(r"(\d+)([A-Za-z_.$][\w.$]*)(?:,(.*))?$", spec)
        if not m:
            raise GerberError(f"AD inválido: {spec}")
        code = int(m.group(1))
        shape = m.group(2)
        params = [float(p) * self._scale for p in m.group(3).split("X")] if m.group(3) else []
        if shape == "C":
            geom = _with_hole(_circle(params[0]), params[1] if len(params) > 1 else None)
            ap = Aperture(code, shape, params, geom, is_circle=True)
        elif shape == "R":
            w, h = params[0], params[1]
            geom = _with_hole(box(-w / 2, -h / 2, w / 2, h / 2), params[2] if len(params) > 2 else None)
            ap = Aperture(code, shape, params, geom)
        elif shape == "O":
            geom = _with_hole(_obround(params[0], params[1]), params[2] if len(params) > 2 else None)
            ap = Aperture(code, shape, params, geom)
        elif shape == "P":
            d, n = params[0], params[1]
            rot = params[2] if len(params) > 2 else 0.0
            geom = _with_hole(_regular_polygon(d, int(n), rot), params[3] if len(params) > 3 else None)
            ap = Aperture(code, shape, params, geom)
        elif shape in self.macros:
            geom = self.macros[shape].instantiate(params)
            ap = Aperture(code, shape, params, geom)
        else:
            raise GerberError(f"apertura {code}: forma/macro desconhecida {shape!r}")
        self.apertures[code] = ap

    # -- palavras (comandos terminados em *) ---------------------------------

    _coord_re = re.compile(r"([XYIJ])([+-]?\d+)")

    def _word(self, w: str):
        if not w or w.startswith("G04"):
            return
        if w == "M02" or w == "M00" or w == "M01":
            return
        # G54D10 (obsoleto) ou D10 -> seleção de apertura
        if w.startswith("G54"):
            w = w[3:]
        if re.fullmatch(r"D\d+", w):
            d = int(w[1:])
            if d >= 10:
                self._select(d)
                return
            # D01/D02/D03 sem coordenadas: repete posição atual
            self._operation(d, None)
            return
        if w in ("G01", "G1", "G02", "G2", "G03", "G3"):
            self.interp = {"G1": "G01", "G2": "G02", "G3": "G03"}.get(w, w)
            return
        if w == "G36":
            self.in_region = True
            self.region_pts = []
            return
        if w == "G37":
            self._close_region()
            self.in_region = False
            return
        if w == "G74":
            self.quadrant = "single"
            return
        if w == "G75":
            self.quadrant = "multi"
            return
        if w in ("G70", "G71", "G90", "G91"):
            if w == "G70":
                self.units, self._scale = "in", 25.4
            elif w == "G71":
                self.units, self._scale = "mm", 1.0
            return
        # bloco de coordenadas com D01/D02/D03 e G01/G02/G03 opcionais no início
        m = re.match(r"^(G0?[123])?((?:[XYIJ][+-]?\d+)+)(D0?[123])?$", w)
        if not m:
            raise GerberError(f"comando não reconhecido: {w!r}")
        if m.group(1):
            g = m.group(1)
            self.interp = {"G1": "G01", "G2": "G02", "G3": "G03"}.get(g, g)
        coords = {k: self._num(v) for k, v in self._coord_re.findall(m.group(2))}
        if not m.group(3):
            # coordenadas sem código D: comportamento obsoleto = repete D01
            self._operation(1, coords)
            return
        self._operation(int(m.group(3)[1:]), coords)

    def _num(self, s: str) -> float:
        sign = -1.0 if s.startswith("-") else 1.0
        digits = s.lstrip("+-")
        total = self.int_digits + self.dec_digits
        if self.zero_omission == "T":
            digits = digits.ljust(total, "0")
        else:
            digits = digits.rjust(total, "0")
        return sign * int(digits) / (10 ** self.dec_digits) * self._scale

    def _select(self, code: int):
        if code not in self.apertures:
            raise GerberError(f"apertura D{code} usada antes de ser definida")
        self.current = self.apertures[code]

    def _operation(self, d: int, coords: dict | None):
        coords = coords or {}
        if self.absolute:
            nx = coords.get("X", self.x)
            ny = coords.get("Y", self.y)
        else:
            nx = self.x + coords.get("X", 0.0)
            ny = self.y + coords.get("Y", 0.0)
        i, j = coords.get("I", 0.0), coords.get("J", 0.0)

        if d == 2:  # move
            if self.in_region and self.region_pts:
                self._close_region()
            self.x, self.y = nx, ny
            if self.in_region:
                self.region_pts = [(nx, ny)]
            return

        if d == 3:  # flash
            if self.current is None:
                raise GerberError("D03 sem apertura selecionada")
            self._add(affinity.translate(self.current.geometry, nx, ny))
            self.x, self.y = nx, ny
            return

        # d == 1: interpolação
        if self.interp == "G01":
            pts = [(self.x, self.y), (nx, ny)]
        else:
            pts = self._arc_points((self.x, self.y), (nx, ny), i, j, self.interp == "G02")

        if self.in_region:
            if not self.region_pts:
                self.region_pts = [(self.x, self.y)]
            self.region_pts.extend(pts[1:])
        else:
            if self.current is None:
                raise GerberError("D01 sem apertura selecionada")
            line = LineString(pts)
            self.paths.append(line)
            if line.length == 0:
                self._add(affinity.translate(self.current.geometry, nx, ny))
            elif self.current.shape != "R":
                self._add(line.buffer(self.current.diameter / 2.0, quad_segs=16))
            else:
                # traço com apertura retangular: área varrida = hull do retângulo
                # no início e no fim de cada segmento
                w, h = self.current.params[0], self.current.params[1]
                rect = box(-w / 2, -h / 2, w / 2, h / 2)
                pieces = [
                    unary_union([affinity.translate(rect, *a), affinity.translate(rect, *b)]).convex_hull
                    for a, b in zip(pts, pts[1:])
                ]
                self._add(unary_union(pieces))
        self.x, self.y = nx, ny

    def _arc_points(self, start, end, i, j, clockwise):
        sx, sy = start
        ex, ey = end
        if self.quadrant == "single":
            # G74: I/J sem sinal; escolher o centro que gera arco <= 90°
            candidates = [(sx + di, sy + dj) for di in (i, -i) for dj in (j, -j)]
            best = None
            for cx, cy in candidates:
                r1 = math.hypot(sx - cx, sy - cy)
                r2 = math.hypot(ex - cx, ey - cy)
                if abs(r1 - r2) > 1e-4 * max(r1, 1):
                    continue
                a0 = math.atan2(sy - cy, sx - cx)
                a1 = math.atan2(ey - cy, ex - cx)
                sweep = (a0 - a1) if clockwise else (a1 - a0)
                sweep %= 2 * math.pi
                if sweep <= math.pi / 2 + 1e-6 and (best is None or sweep < best[0]):
                    best = (sweep, cx, cy)
            if best is None:
                return [start, end]
            _, cx, cy = best
        else:
            cx, cy = sx + i, sy + j

        r = math.hypot(sx - cx, sy - cy)
        a0 = math.atan2(sy - cy, sx - cx)
        a1 = math.atan2(ey - cy, ex - cx)
        if clockwise:
            sweep = (a0 - a1) % (2 * math.pi)
            if sweep < 1e-9 and (abs(sx - ex) > 1e-9 or abs(sy - ey) > 1e-9):
                sweep = 2 * math.pi
            elif sweep < 1e-9:
                sweep = 2 * math.pi  # círculo completo
            sweep = -sweep
        else:
            sweep = (a1 - a0) % (2 * math.pi)
            if sweep < 1e-9:
                sweep = 2 * math.pi
        nseg = max(MIN_ARC_SEGMENTS, int(abs(sweep) * r * ARC_SEGMENTS_PER_MM))
        pts = [(cx + r * math.cos(a0 + sweep * k / nseg), cy + r * math.sin(a0 + sweep * k / nseg))
               for k in range(nseg + 1)]
        pts[0] = start
        pts[-1] = end
        return pts

    def _close_region(self):
        if len(self.region_pts) >= 3:
            poly = Polygon(self.region_pts)
            if not poly.is_valid:
                poly = poly.buffer(0)
            self._add(poly)
        self.region_pts = []

    def _add(self, geom: BaseGeometry):
        if geom.is_empty:
            return
        if self.polarity_dark:
            self.dark.append(geom)
        else:
            self.clear_ops.append((len(self.dark), geom))

    def _finish(self) -> GerberLayer:
        if not self.clear_ops:
            geom = unary_union(self.dark) if self.dark else Polygon()
        else:
            # aplica LPC na ordem: tudo desenhado antes do clear é subtraído,
            # o que vem depois é adicionado por cima.
            geom = Polygon()
            idx = 0
            for at, clear in self.clear_ops:
                if at > idx:
                    geom = unary_union([geom] + self.dark[idx:at])
                    idx = at
                geom = geom.difference(clear)
            if idx < len(self.dark):
                geom = unary_union([geom] + self.dark[idx:])
        return GerberLayer(geometry=geom, paths=self.paths, units="mm")


def parse_gerber(text: str) -> GerberLayer:
    return GerberParser().parse(text)
