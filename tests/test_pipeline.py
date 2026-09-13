"""Testes ponta a ponta com ZIPs reais exportados pelo KiCad 10."""

from pathlib import Path

import pytest

from gcodegen.cli import run
from gcodegen.config import Config
from gcodegen.excellon import parse_excellon
from gcodegen.gerber import parse_gerber
from gcodegen.toolpaths import build_board, generate_toolpaths
from gcodegen.zipreader import read_board_zip

FIX = Path(__file__).parent / "fixtures"


def load(zip_name, cfg):
    files = read_board_zip(FIX / zip_name)
    copper = parse_gerber(files.contents[files.back_copper]).geometry
    outline = parse_gerber(files.contents[files.edge_cuts]).outline_polygon()
    drills = [parse_excellon(files.contents[n]) for n in files.drills]
    return build_board(copper, outline, drills, cfg)


def test_zip_classification():
    files = read_board_zip(FIX / "Arduino_Nano.zip")
    assert files.front_copper == "Arduino_Nano-F_Cu.gtl"
    assert files.back_copper == "Arduino_Nano-B_Cu.gbl"
    assert files.edge_cuts == "Arduino_Nano-Edge_Cuts.gm1"
    assert files.drills == ["Arduino_Nano-NPTH.drl", "Arduino_Nano-PTH.drl"]
    assert files.unknown == ["Arduino_Nano-job.gbrjob"]


def test_nano_board_mirrored_at_origin():
    board = load("Arduino_Nano.zip", Config())
    assert board.mirrored
    assert board.bounds == pytest.approx((0, 0, 43.18, 17.78))
    assert len(board.holes) == 34
    # pad 1 (quadrado) fica em x=3.81 no gerber; espelhado vai para 43.18-3.81
    xs = sorted({round(h.x, 2) for h in board.holes})
    assert 43.18 - 3.81 == pytest.approx(xs[-2])


def test_nano_no_mirror_front():
    board = load("Arduino_Nano.zip", Config(layer="F_Cu"))
    assert not board.mirrored
    xs = sorted({round(h.x, 2) for h in board.holes})
    assert xs[1] == pytest.approx(3.81)


def test_toolpaths_teste_board():
    cfg = Config(drill_single_tool=0.8, mill_holes=True, iso_passes=2)
    board = load("teste.zip", cfg)
    tp = generate_toolpaths(board, cfg)
    assert len(board.outline.interiors) == 1
    assert tp.cutout and tp.cutout_inner
    assert list(tp.drills) == [0.8]
    assert len(tp.drills[0.8]) == 3      # via 0.4 e dois pads 0.8 furados com 0.8
    assert tp.milled_holes == []
    assert len(tp.isolation) > 5
    # todos os caminhos de isolação dentro da placa (com folga da fresa)
    clip = board.outline.buffer(cfg.tool_dia + 0.01)
    from shapely.geometry import LineString
    for p in tp.isolation:
        assert clip.covers(LineString(p))


def test_mill_holes_larger_than_bit():
    cfg = Config(drill_single_tool=0.6, mill_holes=True)
    board = load("teste.zip", cfg)
    tp = generate_toolpaths(board, cfg)
    assert len(tp.drills[0.6]) == 1     # via 0.4
    assert len(tp.milled_holes) == 2    # pads 0.8


def test_cli_end_to_end(tmp_path):
    out = tmp_path / "out"
    assert run([str(FIX / "teste.zip"), "-o", str(out)]) == 0
    names = sorted(p.name for p in out.iterdir())
    assert names == [
        "teste-1-isolacao.nc", "teste-2-furos-0.4mm.nc", "teste-2-furos-0.8mm.nc",
        "teste-3-recorte.nc", "teste-preview.svg",
    ]
    iso = (out / "teste-1-isolacao.nc").read_text()
    assert iso.startswith("(Isolacao")
    assert "G21\nG90\nG94\nG17\nG0 Z2\nM3 S10000\nG4 P1\n" in iso
    assert iso.rstrip().endswith("M5\nG0 X0 Y0\nM2")
    assert "G81" not in iso and "M6" not in iso   # GRBL não tem ciclos fixos / troca
    cut = (out / "teste-3-recorte.nc").read_text()
    assert "Z-1.2" in cut     # subida nas tabs: -1.7 + 0.5
    assert "Z-1.7" in cut
    # nunca abaixo da profundidade de recorte
    zs = [float(tok[1:]) for line in cut.splitlines() for tok in line.split() if tok.startswith("Z")]
    assert min(zs) == -1.7


def test_stepped_drilling():
    from gcodegen.gcode import drill_program
    from gcodegen.toolpaths import Hole
    cfg = Config(drill_depth=-1.0, drill_step=0.3, drill_feed=40)
    text = drill_program([Hole(5, 5, 0.8)], 0.8, cfg)
    zs = [l for l in text.splitlines() if l.startswith("G1 Z")]
    assert zs == ["G1 Z-0.3 F40", "G1 Z-0.6", "G1 Z-0.9", "G1 Z-1"]
    assert "passos de 0.3mm" in text


def test_single_pass_when_step_zero():
    from gcodegen.gcode import drill_program
    from gcodegen.toolpaths import Hole
    text = drill_program([Hole(5, 5, 0.8)], 0.8, Config(drill_depth=-1.0, drill_step=0))
    assert "G1 Z-1 F60" in text and text.count("G1 Z") == 1


def test_milled_hole_uses_same_step():
    from gcodegen.gcode import milled_holes_program
    from gcodegen.toolpaths import Hole
    cfg = Config(drill_depth=-1.0, drill_step=0.5, drill_single_tool=0.8, mill_holes=True)
    text = milled_holes_program([Hole(5, 5, 1.6)], cfg)
    zs = [l for l in text.splitlines() if l.startswith("G1 Z") and "-" in l]
    assert zs == ["G1 Z-0.5 F50", "G1 Z-1 F50"]
