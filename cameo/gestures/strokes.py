"""Stroke geometry for signs drawn with index + middle, measured in palm widths (docs/SIGN-PROTOCOL-PLAN.md, R3).

A palm width ('unit', from the vision worker: the average of five palm distances, about 8 cm on an adult hand) makes a
stroke mean the same at 40 cm and at 90 cm from the camera. Points are kept in camera-frame coordinates (what the HUD
draws) and converted to width units (y times the frame's height / width) whenever something is measured, so a circle
is round and a vertical line is as long as a horizontal one. Pure: no I/O, time is passed in.
"""
import math

from .shapes import fit_circle

UNIT_DEFAULT = .2   # palm width in frame widths when the frame does not say (synthetic tests; a typical live hand is .2-.35)
STEP = .06          # palm widths: shorter moves are tremor and are not added to the path


class Stroke:
    def __init__(self, point, unit, aspect, now):
        self.aspect = aspect
        self.unit = unit
        self.path = [point]          # frame coordinates, for the HUD
        self.start = point
        self.tip = point             # where the fingers are now (the path only grows every STEP)
        self.heading = None
        self.turn = 0.0              # radians, signed: clockwise as the user sees it is positive
        self.travel = 0.0            # farthest distance from the start so far, palm widths
        self.at = now
        self.done = None             # the finished shape, for the HUD
        self.line = None             # 'up' / 'down' / 'left' / 'right' once a line sign engaged

    def w(self, point):
        return point[0], point[1] * self.aspect

    def pw(self, a, b):
        """Distance between two frame points in palm widths."""
        (ax, ay), (bx, by) = self.w(a), self.w(b)
        return math.hypot(bx - ax, by - ay) / self.unit

    def add(self, point, unit):
        self.unit = .8 * self.unit + .2 * unit  # the hand's size drifts slowly; a jump is the worker's job to reject
        self.tip = point
        self.travel = max(self.travel, self.pw(self.start, point))
        last = self.path[-1]
        if self.pw(last, point) < STEP:
            return
        (lx, ly), (px, py) = self.w(last), self.w(point)
        heading = math.atan2(py - ly, px - lx)
        if self.heading is not None:
            self.turn += (heading - self.heading + math.pi) % (2 * math.pi) - math.pi
        self.heading = heading
        self.path.append(point)

    def displacement(self, axis):
        """Signed palm widths from the start to the tip along one axis: right (x) and up (y) are positive."""
        (sx, sy), (tx, ty) = self.w(self.start), self.w(self.tip)
        return (tx - sx) / self.unit if axis == 'x' else (sy - ty) / self.unit

    def line_fit(self):
        """(axis, along, across, bend) of the stroke as a straight line from the start to the tip, palm widths."""
        (sx, sy), (tx, ty) = self.w(self.start), self.w(self.tip)
        dx, dy = tx - sx, ty - sy
        length = math.hypot(dx, dy)
        axis = 'x' if abs(dx) >= abs(dy) else 'y'
        along, across = (abs(dx), abs(dy)) if axis == 'x' else (abs(dy), abs(dx))
        bend = 0.0
        if length:
            for point in self.path + [self.tip]:
                px, py = self.w(point)
                bend = max(bend, abs(dx * (py - sy) - dy * (px - sx)) / length)
        return axis, along / self.unit, across / self.unit, bend / self.unit

    def circle_fit(self):
        """(radius, residual) of the least-squares circle through the path, palm widths; residual is the RMS distance
        of the points from the circle."""
        points = [self.w(p) for p in self.path]
        if len(points) < 5:
            return 0.0, float('inf')
        cx, cy, r = fit_circle(points)
        residual = math.sqrt(sum((math.hypot(x - cx, y - cy) - r) ** 2 for x, y in points) / len(points))
        return r / self.unit, residual / self.unit

    def restart(self, point):
        """A new stroke from here (after a circle, the fingers may draw another shape)."""
        self.path, self.start, self.tip, self.heading, self.turn, self.travel = [point], point, point, None, 0.0, 0.0
