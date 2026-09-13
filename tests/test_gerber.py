import math

import pytest
from shapely.geometry import Point

from gcodegen.gerber import GerberError, eval_macro_expr, parse_gerber

HEADER = "%FSLAX46Y46*%\n%MOMM*%\n%LPD*%\nG01*\n"


def test_macro_expr():
    assert eval_macro_expr("$1+$1", {1: 0.4}) == pytest.approx(0.8)
    assert eval_macro_expr("2x$3", {3: 1.5}) == pytest.approx(3.0)
    assert eval_macro_expr("($1-1)/2", {1: 5}) == pytest.approx(2.0)
    with pytest.raises(GerberError):
        eval_macro_expr("__import__('os')", {})


def test_flash_circle_and_rect():
    g = parse_gerber(HEADER + "%ADD10C,1.000000*%\n%ADD11R,2.000000X1.000000*%\n"
                     "D10*\nX0Y0D03*\nD11*\nX10000000Y0D03*\nM02*")
    assert g.geometry.area == pytest.approx(math.pi * 0.25 + 2.0, rel=0.01)
    minx, miny, maxx, maxy = g.geometry.bounds
    assert (minx, miny, maxx, maxy) == pytest.approx((-0.5, -0.5, 11.0, 0.5))


def test_trace_linear():
    g = parse_gerber(HEADER + "%ADD10C,0.500000*%\nD10*\nX0Y0D02*\nX10000000Y0D01*\nM02*")
    # retângulo 10x0.5 + duas semicircunferências
    assert g.geometry.area == pytest.approx(10 * 0.5 + math.pi * 0.25 ** 2, rel=0.01)
    assert len(g.paths) == 1


def test_arc_ccw_quarter():
    # arco de (10,0) a (0,10) com centro em (0,0), CCW
    g = parse_gerber(HEADER + "%ADD10C,0.200000*%\nD10*\nG75*\nX10000000Y0D02*\n"
                     "G03*\nX0Y10000000I-10000000J0D01*\nM02*")
    line = g.paths[0]
    assert line.length == pytest.approx(math.pi * 10 / 2, rel=0.01)
    # todos os pontos a 10 mm da origem
    for x, y in line.coords:
        assert math.hypot(x, y) == pytest.approx(10, abs=0.01)


def test_arc_single_quadrant_g74():
    g = parse_gerber(HEADER + "%ADD10C,0.200000*%\nD10*\nG74*\nX10000000Y0D02*\n"
                     "G03*\nX0Y10000000I10000000J0D01*\nM02*")
    assert g.paths[0].length == pytest.approx(math.pi * 10 / 2, rel=0.01)


def test_region():
    g = parse_gerber(HEADER + "G36*\nX0Y0D02*\nX10000000Y0D01*\nX10000000Y10000000D01*\n"
                     "X0Y10000000D01*\nX0Y0D01*\nG37*\nM02*")
    assert g.geometry.area == pytest.approx(100.0)


def test_clear_polarity():
    g = parse_gerber(HEADER + "%ADD10R,10.000000X10.000000*%\n%ADD11C,2.000000*%\n"
                     "D10*\nX0Y0D03*\n%LPC*%\nD11*\nX0Y0D03*\n%LPD*%\nM02*")
    assert g.geometry.area == pytest.approx(100 - math.pi, rel=0.01)
    assert not g.geometry.contains(Point(0, 0))


def test_inches_and_trailing_zeros():
    g = parse_gerber("%FSTAX24Y24*%\n%MOIN*%\n%ADD10C,0.1*%\nD10*\nX01Y0D03*\nM02*")
    # X01 com zeros à direita omitidos em 2.4 => 01.0000 in = 25.4 mm
    assert g.geometry.centroid.x == pytest.approx(25.4)
    assert g.geometry.area == pytest.approx(math.pi * (2.54 / 2) ** 2, rel=0.01)


def test_kicad_roundrect_macro():
    text = HEADER + """%AMRoundRect*
0 Rectangle with rounded corners*
0 $1 Rounding radius*
0 $2 $3 $4 $5 $6 $7 $8 $9 X,Y pos of 4 extreme points*
0 Add a 4 corners polygon primitive as box body*
4,1,4,$2,$3,$4,$5,$6,$7,$8,$9,$2,$3,0*
0 Add four circle primitives for the rounded corners*
1,1,$1+$1,$2,$3*
1,1,$1+$1,$4,$5*
1,1,$1+$1,$6,$7*
1,1,$1+$1,$8,$9*
0 Add four rect primitives between the rounded corners*
20,1,$1+$1,$2,$3,$4,$5,0*
20,1,$1+$1,$4,$5,$6,$7,0*
20,1,$1+$1,$6,$7,$8,$9,0*
20,1,$1+$1,$8,$9,$2,$3,0*%
%ADD10RoundRect,0.400000X-0.400000X-0.400000X0.400000X-0.400000X0.400000X0.400000X-0.400000X0.400000X0*%
D10*
X0Y0D03*
M02*"""
    g = parse_gerber(text)
    # quadrado 1.6 x 1.6 com cantos r=0.4: área = 1.6² - (4 - π)·0.4²
    assert g.geometry.area == pytest.approx(1.6 ** 2 - (4 - math.pi) * 0.16, rel=0.01)
    assert g.geometry.bounds == pytest.approx((-0.8, -0.8, 0.8, 0.8), abs=1e-3)


def test_outline_polygon_with_hole():
    text = HEADER + ("%ADD10C,0.100000*%\nD10*\n"
                     "X0Y0D02*\nX30000000Y0D01*\nX30000000Y20000000D01*\nX0Y20000000D01*\nX0Y0D01*\n"
                     "X5500000Y16000000D02*\nG75*\nG02*\nX2500000Y16000000I-1500000J0D01*\nG01*\n"
                     "X2500000Y16000000D02*\nG75*\nG02*\nX5500000Y16000000I1500000J0D01*\nG01*\nM02*")
    outline = parse_gerber(text).outline_polygon()
    assert outline is not None
    assert len(outline.interiors) == 1
    assert outline.area == pytest.approx(600 - math.pi * 1.5 ** 2, rel=0.01)


def test_unknown_aperture_raises():
    with pytest.raises(GerberError):
        parse_gerber(HEADER + "D99*\nX0Y0D03*\nM02*")


def test_robust_union_handles_invalid_pieces():
    from shapely.geometry import Polygon
    from gcodegen.gerber import robust_union, robust_difference
    bow = Polygon([(0, 0), (1, 1), (1, 0), (0, 1)])       # auto-intersecta
    sq = Polygon([(0.5, 0.5), (2, 0.5), (2, 2), (0.5, 2)])
    u = robust_union([bow, sq])
    assert u.is_valid and u.area == pytest.approx(0.5 + 2.25 - 0.125, abs=1e-3)
    d = robust_difference(bow, Point(0.5, 0.5).buffer(0.1))
    assert d.is_valid and d.area < 0.5


def test_degenerate_gerber_does_not_raise():
    # traço de comprimento zero + arco de raio zero + região com pontos repetidos
    text = HEADER + ("%ADD10C,0.200000*%\nD10*\n"
                     "X1000000Y1000000D02*\nX1000000Y1000000D01*\n"
                     "G75*\nG03*\nX1000000Y1000000I0J0D01*\nG01*\n"
                     "G36*\nX0Y0D02*\nX2000000Y0D01*\nX2000000Y0D01*\nX2000000Y2000000D01*\n"
                     "X0Y2000000D01*\nX0Y0D01*\nG37*\nM02*")
    g = parse_gerber(text)
    assert g.geometry.is_valid and g.geometry.area > 3.9
