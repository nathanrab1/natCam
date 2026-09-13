"""Parser Excellon (arquivos .drl do KiCad).

Suporta formato decimal ("FORMAT={-:-/ absolute / metric / decimal}") e o
formato com zeros suprimidos (ex.: METRIC,TZ com 3:3), unidades INCH/METRIC,
seleção de ferramenta Tn e rasgos (G85) — estes viram uma lista de furos.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


class ExcellonError(Exception):
    pass


@dataclass
class DrillTool:
    number: int
    diameter: float                      # mm
    holes: list[tuple[float, float]] = field(default_factory=list)
    slots: list[tuple[tuple[float, float], tuple[float, float]]] = field(default_factory=list)


@dataclass
class DrillFile:
    tools: list[DrillTool]

    @property
    def hole_count(self) -> int:
        return sum(len(t.holes) for t in self.tools)


class ExcellonParser:
    def __init__(self):
        self.units = "mm"
        self.decimal = True
        self.int_digits = 3
        self.dec_digits = 3
        self.zero_omission = "L"   # L = leading omitido (TZ: trailing mantido)
        self.tools: dict[int, DrillTool] = {}
        self.current: DrillTool | None = None
        self.in_header = True
        self.x = 0.0
        self.y = 0.0
        self.absolute = True

    def parse(self, text: str) -> DrillFile:
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith(";"):
                self._comment(line)
                continue
            if self.in_header:
                self._header(line)
            else:
                self._body(line)
        return DrillFile(tools=[self.tools[k] for k in sorted(self.tools)])

    # -- cabeçalho -----------------------------------------------------------

    def _comment(self, line: str):
        # KiCad: "; FORMAT={-:-/ absolute / metric / decimal}" ou
        #        "; FORMAT={3:3/ absolute / metric / suppress leading zeros}"
        m = re.search(r"FORMAT=\{(\S+):(\S+)/\s*(\w+)\s*/\s*(\w+)\s*/\s*([^}]+)\}", line)
        if m:
            i, d = m.group(1), m.group(2)
            if i != "-" and d != "-":
                self.int_digits, self.dec_digits = int(i), int(d)
            self.absolute = m.group(3).strip() == "absolute"
            self.units = "in" if m.group(4).strip() == "inch" else "mm"
            mode = m.group(5).strip()
            self.decimal = mode == "decimal"
            if "leading" in mode:
                self.zero_omission = "L"
            elif "trailing" in mode:
                self.zero_omission = "T"

    def _header(self, line: str):
        if line == "M48":
            return
        if line in ("%", "M95"):
            self.in_header = False
            return
        if line.startswith(("METRIC", "INCH")):
            self.units = "mm" if line.startswith("METRIC") else "in"
            parts = line.split(",")
            for p in parts[1:]:
                p = p.strip()
                if p == "TZ":
                    self.zero_omission = "L"   # zeros à direita mantidos
                elif p == "LZ":
                    self.zero_omission = "T"   # zeros à esquerda mantidos
                elif re.fullmatch(r"0+\.0+", p):
                    ip, dp = p.split(".")
                    self.int_digits, self.dec_digits = len(ip), len(dp)
                    self.decimal = False
            if self.units == "in" and not any(re.fullmatch(r"0+\.0+", p.strip()) for p in parts[1:]):
                self.int_digits, self.dec_digits = 2, 4
            return
        if line.startswith(("FMAT", "VER", "ICI", "ATC", "DETECT", "M71", "M72")):
            return
        m = re.match(r"^T(\d+)(?:[FSBHZ]\d*)*C([\d.]+)", line)
        if m:
            n = int(m.group(1))
            d = float(m.group(2)) * (25.4 if self.units == "in" else 1.0)
            self.tools[n] = DrillTool(n, round(d, 4))
            return
        if line.startswith("T"):
            raise ExcellonError(f"definição de ferramenta inválida: {line}")
        # outras linhas do cabeçalho são ignoradas

    # -- corpo ---------------------------------------------------------------

    def _body(self, line: str):
        if line in ("M30", "M00", "M02"):
            return
        if line in ("G90", "G05", "G81", "M71", "M72", "G93"):
            if line == "M71":
                self.units = "mm"
            elif line == "M72":
                self.units = "in"
            return
        if line == "G91":
            self.absolute = False
            return
        m = re.fullmatch(r"T(\d+)", line)
        if m:
            n = int(m.group(1))
            if n == 0:
                self.current = None
                return
            if n not in self.tools:
                raise ExcellonError(f"ferramenta T{n} usada sem definição")
            self.current = self.tools[n]
            return
        if line.startswith("G85") or "G85" in line:
            # rasgo: X..Y..G85X..Y..
            a, _, b = line.partition("G85")
            p1 = self._xy(a)
            p2 = self._xy(b)
            self._need_tool().slots.append((p1, p2))
            self.x, self.y = p2
            return
        if line.startswith(("X", "Y")) or line.startswith("G00") or line.startswith("G01"):
            if line.startswith("G0"):
                return  # G00/G01 (rota) — só usado para rasgos, ignorado aqui
            p = self._xy(line)
            self._need_tool().holes.append(p)
            self.x, self.y = p
            return
        if line.startswith(("G", "M")):
            return  # códigos não relevantes
        raise ExcellonError(f"linha não reconhecida no corpo: {line}")

    def _need_tool(self) -> DrillTool:
        if self.current is None:
            raise ExcellonError("coordenada sem ferramenta selecionada")
        return self.current

    def _xy(self, s: str) -> tuple[float, float]:
        vals = dict(re.findall(r"([XY])([+-]?[\d.]+)", s))
        x = self._num(vals["X"]) if "X" in vals else self.x
        y = self._num(vals["Y"]) if "Y" in vals else self.y
        if not self.absolute:
            x += self.x if "X" in vals else 0
            y += self.y if "Y" in vals else 0
        return (x, y)

    def _num(self, s: str) -> float:
        scale = 25.4 if self.units == "in" else 1.0
        if "." in s or self.decimal:
            return float(s) * scale
        sign = -1.0 if s.startswith("-") else 1.0
        digits = s.lstrip("+-")
        total = self.int_digits + self.dec_digits
        if self.zero_omission == "T":
            digits = digits.ljust(total, "0")
        else:
            digits = digits.rjust(total, "0")
        return sign * int(digits) / (10 ** self.dec_digits) * scale


def parse_excellon(text: str) -> DrillFile:
    return ExcellonParser().parse(text)
