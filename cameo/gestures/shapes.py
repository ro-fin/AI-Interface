"""Clean shapes for the HUD (user's request, 2026-09-28): a shortcut drawn roughly in the air is shown as the shape it was
recognised as, a true circle or a straight line, instead of the wobbly fingertip path. Pure geometry, screen pixels in
and out (a circle fitted in camera coordinates would be an ellipse on a screen of another aspect ratio)."""
import math


def fit_circle(points):
    """Least-squares circle through the points (Kasa fit): (cx, cy, r). A partial arc still finds its true centre,
    where the average of the points would be pulled toward the drawn side."""
    n = len(points)
    mx, my = sum(p[0] for p in points) / n, sum(p[1] for p in points) / n
    # Centred coordinates keep the 3x3 system well conditioned at screen-pixel sizes.
    u = [(p[0] - mx, p[1] - my) for p in points]
    suu = sum(a * a for a, _ in u)
    svv = sum(b * b for _, b in u)
    suv = sum(a * b for a, b in u)
    ru = sum(a * (a * a + b * b) for a, b in u) / 2
    rv = sum(b * (a * a + b * b) for a, b in u) / 2
    det = suu * svv - suv * suv
    if abs(det) < 1e-9:
        return mx, my, sum(math.hypot(a, b) for a, b in u) / n  # a straight or single-point path
    uc = (ru * svv - rv * suv) / det
    vc = (suu * rv - suv * ru) / det
    r = math.sqrt(uc * uc + vc * vc + (suu + svv) / n)
    return mx + uc, my + vc, r


def circle(points, segments=64):
    """The fitted circle as a closed polyline."""
    cx, cy, r = fit_circle(points)
    return [(cx + r * math.cos(2 * math.pi * i / segments), cy + r * math.sin(2 * math.pi * i / segments)) for i in range(segments + 1)]


def line(points):
    """The stroke as a straight line along its main axis (the line signs are vertical or horizontal): from the first
    point to the last, at the stroke's average position across the axis."""
    (x0, y0), (x1, y1) = points[0], points[-1]
    if abs(x1 - x0) >= abs(y1 - y0):
        y = sum(p[1] for p in points) / len(points)
        return [(x0, y), (x1, y)]
    x = sum(p[0] for p in points) / len(points)
    return [(x, y0), (x, y1)]
