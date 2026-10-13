"""One Euro filter: steady when the hand is still, low lag when it moves quickly."""
import math


class OneEuro:
    def __init__(self, min_cutoff=1.5, beta=.02, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.reset()

    def reset(self):
        self.value = self.speed = self.at = None

    @staticmethod
    def alpha(cutoff, dt):
        tau = 1 / (2 * math.pi * cutoff)
        return 1 / (1 + tau / dt)

    def __call__(self, value, now):
        if self.at is None or now <= self.at:
            self.value, self.speed, self.at = value, 0.0, now
            return value
        dt = now - self.at
        a = self.alpha(self.d_cutoff, dt)
        self.speed = a * (value - self.value) / dt + (1 - a) * self.speed
        a = self.alpha(self.min_cutoff + self.beta * abs(self.speed), dt)
        self.value = a * value + (1 - a) * self.value
        self.at = now
        return self.value
