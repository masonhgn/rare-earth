
# transient visual effects: in-progress break state + procedural chunk
# particles. all rendering is procedural (filled surfaces) — no extra
# sprite assets required.

import math
import random
from dataclasses import dataclass


@dataclass
class BreakState:
    # what the player is currently breaking. one at a time.
    # entity_id=None means we're breaking the overlay tile at `tile` (ore
    # patches, etc) rather than a placed entity instance. either way the
    # `tile` is canonical for visuals and the cursor-still-on-target check.
    start_ms: int
    duration_ms: int
    tile: tuple[int, int]
    entity_id: str | None = None

    def progress(self, now_ms: int) -> float:
        if self.duration_ms <= 0:
            return 1.0
        return min(1.0, max(0.0, (now_ms - self.start_ms) / self.duration_ms))

    def is_complete(self, now_ms: int) -> bool:
        return now_ms - self.start_ms >= self.duration_ms


@dataclass
class Particle:
    # 1-tile-scale debris chunk used for break bursts. position is world-space;
    # the renderer transforms via camera. gravity is applied in tick().
    world_x: float
    world_y: float
    vx: float
    vy: float
    born_ms: int
    lifetime_ms: int
    color: tuple[int, int, int] = (60, 60, 60)
    size: int = 4

    def alive(self, now_ms: int) -> bool:
        return now_ms - self.born_ms < self.lifetime_ms

    def tick(self, dt: float) -> None:
        self.world_x += self.vx * dt
        self.world_y += self.vy * dt
        # crude gravity so chunks arc downward and land
        self.vy += 520 * dt


def spawn_break_chunks(
    world_pos: tuple[float, float],
    now_ms: int,
    count: int = 8,
    color: tuple[int, int, int] = (70, 70, 70),
) -> list[Particle]:
    # radial burst from the broken tile's center, biased slightly upward so
    # the chunks arc rather than slide flat. lifetime + size are jittered
    # so the burst doesn't look like a mechanical pattern.
    cx, cy = world_pos
    out: list[Particle] = []
    for _ in range(count):
        angle = random.uniform(0.0, 2 * math.pi)
        speed = random.uniform(80, 200)
        vx = math.cos(angle) * speed
        vy = math.sin(angle) * speed - 120
        lifetime = random.randint(450, 900)
        size = random.randint(3, 6)
        out.append(Particle(
            world_x=cx, world_y=cy,
            vx=vx, vy=vy,
            born_ms=now_ms, lifetime_ms=lifetime,
            color=color, size=size,
        ))
    return out
