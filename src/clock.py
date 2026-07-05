
# day clock. tracks real-time elapsed seconds and derives the current
# day number from DAY_LENGTH_SEC. fires on_rollover exactly once per
# day boundary crossing so subscribers (autosave, contract resolution,
# spot price walks if we ever tie them to day-roll) can hook in.
#
# day 1 is the starting day. elapsed=0 -> day 1. elapsed=DAY_LENGTH_SEC
# -> day 2 (first rollover).

from config import DAY_LENGTH_SEC


class DayClock:
    def __init__(self, *, elapsed: float = 0.0) -> None:
        self.elapsed = float(elapsed)
        self._last_day = self.day
        self.on_rollover = lambda new_day: None

    @property
    def day(self) -> int:
        return int(self.elapsed // DAY_LENGTH_SEC) + 1

    def tick(self, dt: float) -> None:
        self.elapsed += dt
        current = self.day
        if current != self._last_day:
            self._last_day = current
            self.on_rollover(current)
