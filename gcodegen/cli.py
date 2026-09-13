"""Interface de linha de comando."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .pipeline import generate_toolpaths, load_board, make_board, write_programs


def build_parser() -> argparse.ArgumentParser:
    d = Config()
    p = argparse.ArgumentParser(
        prog="gcodegen",
        description="Gera G-code GRBL (isolação, furação e recorte) a partir do ZIP de Gerber + Excellon do KiCad.",
    )
    p.add_argument("input", help="ZIP exportado pelo KiCad (ou pasta com os arquivos)")
    p.add_argument("-o", "--output", default=None, help="pasta de saída (padrão: <nome>_gcode ao lado do ZIP)")
    p.add_argument("--layer", choices=["B_Cu", "F_Cu"], default=d.layer, help="camada de cobre a fresar")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--mirror", dest="mirror", action="store_true", default=None, help="força espelhar em X")
    g.add_argument("--no-mirror", dest="mirror", action="store_false", help="não espelha (padrão: espelha só B_Cu)")
    p.add_argument("--origin", choices=["board", "gerber"], default=d.origin,
                   help="board = canto inferior-esquerdo da placa em X0 Y0; gerber = coordenadas originais")

    iso = p.add_argument_group("isolação")
    iso.add_argument("--tool-dia", type=float, default=d.tool_dia, help="largura de corte da fresa V na profundidade (mm)")
    iso.add_argument("--iso-passes", type=int, default=d.iso_passes, help="número de passes concêntricos")
    iso.add_argument("--iso-overlap", type=float, default=d.iso_overlap, help="sobreposição entre passes (0-1)")
    iso.add_argument("--iso-depth", type=float, default=d.iso_depth, help="Z de corte da isolação (negativo)")
    iso.add_argument("--iso-feed", type=float, default=d.iso_feed, help="avanço XY (mm/min)")
    iso.add_argument("--skip-isolation", action="store_true")

    dr = p.add_argument_group("furação")
    dr.add_argument("--drill-depth", type=float, default=d.drill_depth)
    dr.add_argument("--drill-feed", type=float, default=d.drill_feed, help="avanço em Z ao furar (mm/min)")
    dr.add_argument("--drill-step", type=float, default=d.drill_step,
                    help="quanto desce por vez em cada furo, reto ou em círculo (0 = de uma vez)")
    dr.add_argument("--drill-single-tool", type=float, default=None, metavar="DIA",
                    help="usa uma única broca (mm) para todos os furos, em um único arquivo")
    dr.add_argument("--mill-holes", action="store_true",
                    help="com --drill-single-tool: furos maiores que a broca são fresados em círculo")
    dr.add_argument("--skip-drill", action="store_true")

    ct = p.add_argument_group("recorte")
    ct.add_argument("--cut-tool-dia", type=float, default=d.cut_tool_dia)
    ct.add_argument("--cut-depth", type=float, default=d.cut_depth)
    ct.add_argument("--cut-step", type=float, default=d.cut_step, help="profundidade por passe")
    ct.add_argument("--cut-feed", type=float, default=d.cut_feed)
    ct.add_argument("--tabs", type=int, default=d.tabs, help="número de tabs (0 desliga)")
    ct.add_argument("--tab-width", type=float, default=d.tab_width)
    ct.add_argument("--tab-height", type=float, default=d.tab_height)
    ct.add_argument("--skip-cutout", action="store_true")

    ge = p.add_argument_group("geral")
    ge.add_argument("--safe-z", type=float, default=d.safe_z)
    ge.add_argument("--plunge-feed", type=float, default=d.plunge_feed)
    ge.add_argument("--spindle", type=int, default=d.spindle, help="RPM (S no M3)")
    ge.add_argument("--dwell", type=float, default=d.dwell, help="segundos de espera após M3")
    ge.add_argument("--no-preview", action="store_true", help="não gera preview.svg")
    return p


def config_from_args(a: argparse.Namespace) -> Config:
    return Config(
        layer=a.layer, mirror=a.mirror, origin=a.origin,
        tool_dia=a.tool_dia, iso_passes=a.iso_passes, iso_overlap=a.iso_overlap,
        iso_depth=a.iso_depth, iso_feed=a.iso_feed,
        drill_depth=a.drill_depth, drill_feed=a.drill_feed,
        drill_step=a.drill_step,
        drill_single_tool=a.drill_single_tool, mill_holes=a.mill_holes,
        cut_tool_dia=a.cut_tool_dia, cut_depth=a.cut_depth, cut_step=a.cut_step,
        cut_feed=a.cut_feed, tabs=a.tabs, tab_width=a.tab_width, tab_height=a.tab_height,
        safe_z=a.safe_z, plunge_feed=a.plunge_feed, spindle=a.spindle, dwell=a.dwell,
    )


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = config_from_args(args)
    src = Path(args.input)
    if not src.exists():
        print(f"erro: {src} não existe", file=sys.stderr)
        return 1
    loaded = load_board(src)
    if not loaded.has_layer(cfg.layer) and not args.skip_isolation:
        print(f"erro: camada {cfg.layer} não encontrada no ZIP. Arquivos: {sorted(loaded.files.contents)}",
              file=sys.stderr)
        return 1
    board = make_board(loaded, cfg)
    tp = generate_toolpaths(board, cfg)
    out = Path(args.output) if args.output else src.with_name(f"{loaded.name}_gcode")
    write_programs(loaded.name, board, tp, cfg, out,
                   isolation=not args.skip_isolation, drill=not args.skip_drill,
                   cutout=not args.skip_cutout, preview=not args.no_preview)
    return 0


def main():
    sys.exit(run())
