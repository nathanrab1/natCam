"""Identifica os arquivos relevantes dentro do ZIP exportado pelo KiCad."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class BoardFiles:
    front_copper: str | None = None
    back_copper: str | None = None
    edge_cuts: str | None = None
    drills: list[str] = field(default_factory=list)      # PTH, NPTH ou combinado
    contents: dict[str, str] = field(default_factory=dict)
    unknown: list[str] = field(default_factory=list)


# padrões por nome (KiCad usa "-F_Cu.gbr" ou ".gtl" no modo Protel)
_PATTERNS = {
    "front_copper": [r"-F_Cu\.(gbr|gtl)$", r"\.gtl$", r"-F\.Cu\.gbr$"],
    "back_copper": [r"-B_Cu\.(gbr|gbl)$", r"\.gbl$", r"-B\.Cu\.gbr$"],
    "edge_cuts": [r"-Edge_Cuts\.(gbr|gm1|gko)$", r"\.gm1$", r"\.gko$", r"-Edge\.Cuts\.gbr$"],
}
_DRILL = [r"\.drl$", r"\.xln$", r"\.txt$"]


def _match(name: str, patterns: list[str]) -> bool:
    return any(re.search(p, name, re.IGNORECASE) for p in patterns)


def _is_gerber(text: str) -> bool:
    return "%FSLA" in text[:2000] or "%MOMM" in text[:2000] or "%MOIN" in text[:2000]


def _is_excellon(text: str) -> bool:
    head = text[:500]
    return head.lstrip().startswith("M48") or "METRIC" in head or "INCH" in head


def _classify_by_content(name: str, text: str, files: BoardFiles) -> bool:
    """Fallback: usa os atributos TF.FileFunction do Gerber X2."""
    m = re.search(r"%TF\.FileFunction,([^*]+)\*%", text[:3000])
    if not m:
        return False
    fn = m.group(1)
    if fn.startswith("Copper,") and fn.endswith(",Top"):
        files.front_copper = files.front_copper or name
        return True
    if fn.startswith("Copper,") and fn.endswith(",Bot"):
        files.back_copper = files.back_copper or name
        return True
    if fn.startswith("Profile"):
        files.edge_cuts = files.edge_cuts or name
        return True
    return False


def read_board_zip(path: str | Path) -> BoardFiles:
    files = BoardFiles()
    with zipfile.ZipFile(path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = Path(info.filename).name
            if name.startswith("._") or name.startswith("."):
                continue  # metadados do macOS
            raw = zf.read(info)
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                text = raw.decode("latin-1")
            files.contents[name] = text
            if _match(name, _DRILL) and _is_excellon(text):
                files.drills.append(name)
            elif _match(name, _PATTERNS["edge_cuts"]) and _is_gerber(text):
                files.edge_cuts = name
            elif _match(name, _PATTERNS["front_copper"]) and _is_gerber(text):
                files.front_copper = name
            elif _match(name, _PATTERNS["back_copper"]) and _is_gerber(text):
                files.back_copper = name
            elif _is_gerber(text) and _classify_by_content(name, text, files):
                pass
            elif _is_excellon(text):
                files.drills.append(name)
            else:
                files.unknown.append(name)
    files.drills.sort()
    return files


def read_board_dir(path: str | Path) -> BoardFiles:
    """Mesma classificação, mas para uma pasta (útil em testes)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for p in Path(path).iterdir():
            if p.is_file():
                zf.write(p, p.name)
    buf.seek(0)
    return read_board_zip(buf)
