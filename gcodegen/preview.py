"""Preview SVG do cobre, contorno, furos e caminhos gerados."""

from __future__ import annotations

from shapely.geometry import MultiPolygon, Polygon

from .toolpaths import Board, Toolpaths


def _poly_d(p: Polygon) -> str:
    def ring(coords):
        return "M " + " L ".join(f"{x:.3f} {y:.3f}" for x, y in coords) + " Z"
    return " ".join([ring(p.exterior.coords)] + [ring(h.coords) for h in p.interiors])


def _path_d(pts) -> str:
    return "M " + " L ".join(f"{x:.3f} {y:.3f}" for x, y in pts)


def render_svg(board: Board, tp: Toolpaths, drill_tool_dia: float | None) -> str:
    minx, miny, maxx, maxy = board.bounds
    pad = 3.0
    minx, miny, maxx, maxy = minx - pad, miny - pad, maxx + pad, maxy + pad
    w, h = maxx - minx, maxy - miny
    scale = 10  # px por mm
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w*scale:.0f}" height="{h*scale:.0f}" '
        f'viewBox="{minx:.3f} {-maxy:.3f} {w:.3f} {h:.3f}">',
        '<rect x="%.3f" y="%.3f" width="%.3f" height="%.3f" fill="#f4efe6"/>' % (minx, -maxy, w, h),
        '<g transform="scale(1,-1)">',  # Y para cima
    ]
    if board.outline is not None:
        out.append(f'<path d="{_poly_d(board.outline)}" fill="#d9c9a3" stroke="none"/>')
    polys = [board.copper] if isinstance(board.copper, Polygon) else (
        list(board.copper.geoms) if isinstance(board.copper, MultiPolygon) else [])
    for p in polys:
        out.append(f'<path d="{_poly_d(p)}" fill="#c47a2c" fill-rule="evenodd" stroke="none"/>')
    for path in tp.isolation:
        out.append(f'<path d="{_path_d(path)}" fill="none" stroke="#c0392b" stroke-width="0.08"/>')
    for d, holes in tp.drills.items():
        for hh in holes:
            out.append(f'<circle cx="{hh.x:.3f}" cy="{hh.y:.3f}" r="{d/2:.3f}" fill="#f4efe6" stroke="#2c5fc0" stroke-width="0.06"/>')
    for hh in tp.milled_holes:
        out.append(f'<circle cx="{hh.x:.3f}" cy="{hh.y:.3f}" r="{hh.diameter/2:.3f}" fill="#f4efe6" stroke="#8e44ad" stroke-width="0.06"/>')
    for path in tp.cutout + tp.cutout_inner:
        out.append(f'<path d="{_path_d(path)}" fill="none" stroke="#1e8449" stroke-width="0.15" stroke-dasharray="1 0.5"/>')
    # eixos na origem
    out.append('<path d="M 0 0 L 5 0" stroke="#e00" stroke-width="0.2"/>')
    out.append('<path d="M 0 0 L 0 5" stroke="#0a0" stroke-width="0.2"/>')
    out.append("</g></svg>")
    return "\n".join(out)
