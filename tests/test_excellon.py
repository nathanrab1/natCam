import pytest

from gcodegen.excellon import parse_excellon


def test_decimal_format():
    text = """M48
; DRILL file KiCad 10.0.1
; FORMAT={-:-/ absolute / metric / decimal}
FMAT,2
METRIC
T1C1.000
T2C0.600
%
G90
G05
T1
X103.81Y-83.49
X106.35Y-98.73
T2
X1.5Y2.25
M30
"""
    df = parse_excellon(text)
    assert [t.diameter for t in df.tools] == [1.0, 0.6]
    assert df.tools[0].holes == [(103.81, -83.49), (106.35, -98.73)]
    assert df.tools[1].holes == [(1.5, 2.25)]
    assert df.hole_count == 3


def test_suppress_leading_zeros_33():
    text = """M48
; FORMAT={3:3/ absolute / metric / suppress leading zeros}
FMAT,2
METRIC,TZ
T1C0.400
%
G90
G05
T1
X115000Y-113810
X5Y-5
M30
"""
    df = parse_excellon(text)
    assert df.tools[0].holes[0] == pytest.approx((115.0, -113.81))
    # "X5" com leading zeros suprimidos, 3.3 => 000.005
    assert df.tools[0].holes[1] == pytest.approx((0.005, -0.005))


def test_inch_24_leading_zeros_kept():
    text = """M48
; FORMAT={2:4/ absolute / inch / suppress trailing zeros}
FMAT,2
INCH,LZ
T1C0.0320
%
G90
G05
T1
X01Y005
M30
"""
    df = parse_excellon(text)
    assert df.tools[0].diameter == pytest.approx(0.8128)
    # LZ: zeros à esquerda mantidos, trailing omitidos: X01 => 01.0000 in
    assert df.tools[0].holes[0] == pytest.approx((25.4, 12.7))


def test_slot_g85():
    text = "M48\nMETRIC\nT1C1.0\n%\nT1\nX10.0Y10.0G85X14.0Y10.0\nM30\n"
    df = parse_excellon(text)
    assert df.tools[0].slots == [((10.0, 10.0), (14.0, 10.0))]
