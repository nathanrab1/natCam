"""Fluxo completo compartilhado pela CLI e pela interface gráfica."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from shapely.geometry import Polygon

from .config import Config
from .excellon import parse_excellon
from .gcode import cutout_program, drill_program, estimate_time, isolation_program, milled_holes_program
from .gerber import parse_gerber
from .preview import render_svg
from .toolpaths import Board, Toolpaths, build_board, generate_toolpaths
from .zipreader import BoardFiles, read_board_dir, read_board_zip

Log = Callable[[str], None]


@dataclass
class LoadedBoard:
    """Geometria bruta do ZIP, ainda em coordenadas do gerber."""

    name: str
    files: BoardFiles
    copper: dict[str, object]            # "B_Cu"/"F_Cu" -> geometria
    outline: Polygon | None
    drills: list

    def has_layer(self, layer: str) -> bool:
        return layer in self.copper


def load_board(path: str | Path, log: Log = print) -> LoadedBoard:
    src = Path(path)
    files = read_board_dir(src) if src.is_dir() else read_board_zip(src)
    log(f"Arquivos: F_Cu={files.front_copper}  B_Cu={files.back_copper}  "
        f"contorno={files.edge_cuts}  furos={files.drills}")
    if files.unknown:
        log(f"  ignorados: {files.unknown}")

    copper = {}
    if files.back_copper:
        copper["B_Cu"] = parse_gerber(files.contents[files.back_copper]).geometry
    if files.front_copper:
        copper["F_Cu"] = parse_gerber(files.contents[files.front_copper]).geometry
    outline = None
    if files.edge_cuts:
        outline = parse_gerber(files.contents[files.edge_cuts]).outline_polygon()
        if outline is None:
            log("aviso: Edge.Cuts não forma um polígono fechado; recorte desativado")
    drills = [parse_excellon(files.contents[n]) for n in files.drills]
    return LoadedBoard(src.stem, files, copper, outline, drills)


def make_board(loaded: LoadedBoard, cfg: Config) -> Board:
    copper = loaded.copper.get(cfg.layer, Polygon())
    return build_board(copper, loaded.outline, loaded.drills, cfg)


def write_programs(name: str, board: Board, tp: Toolpaths, cfg: Config, out: Path,
                   log: Log = print, isolation=True, drill=True, cutout=True, preview=True) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    bx0, by0, bx1, by1 = board.bounds
    log(f"Placa: {bx1-bx0:.2f} x {by1-by0:.2f} mm  espelhada={'sim' if board.mirrored else 'não'}  "
        f"furos={len(board.holes)}")
    written: list[Path] = []

    def save(fname: str, text: str, what: str):
        path = out / fname
        path.write_text(text)
        written.append(path)
        log(f"  {what:<28} -> {path.name}  (~{estimate_time(text):.1f} min)")

    if isolation and not board.copper.is_empty:
        save(f"{name}-1-isolacao.nc", isolation_program(tp, cfg),
             f"isolação ({len(tp.isolation)} caminhos)")
    if drill:
        for dia, holes in tp.drills.items():
            save(f"{name}-2-furos-{dia:g}mm.nc", drill_program(holes, dia, cfg),
                 f"furos {dia:g}mm ({len(holes)})")
        if tp.milled_holes:
            save(f"{name}-2-furos-fresados.nc", milled_holes_program(tp.milled_holes, cfg),
                 f"furos fresados ({len(tp.milled_holes)})")
    if cutout and tp.cutout:
        save(f"{name}-3-recorte.nc", cutout_program(tp, cfg), "recorte")
    if preview:
        svg = out / f"{name}-preview.svg"
        svg.write_text(render_svg(board, tp, cfg.drill_single_tool))
        written.append(svg)
        log(f"  preview                      -> {svg.name}")
    return written


__all__ = ["LoadedBoard", "load_board", "make_board", "generate_toolpaths", "write_programs"]
