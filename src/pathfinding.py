
# A* pathfinding on the tile grid.
#
# 8-directional movement with diagonal cost √2. uses the octile distance
# heuristic which is admissible + consistent for 8-dir grids. solids
# are blocked via World.is_walkable; non-solid entities (rocks) and
# overlay tiles (ore patches) are walkable.
#
# diagonals through solid corners are blocked — without that check the
# player can "slip" between two solids that share a diagonal corner.

import heapq
import math


_SQRT2 = math.sqrt(2)

# (dx, dy, cost) for 8-directional neighbors. cardinal cost 1, diagonal √2.
_NEIGHBORS = [
    (-1,  0, 1.0), (1, 0, 1.0), (0, -1, 1.0), (0, 1, 1.0),
    (-1, -1, _SQRT2), (1, -1, _SQRT2), (-1, 1, _SQRT2), (1, 1, _SQRT2),
]


def find_path(world, start: tuple[int, int], goal: tuple[int, int]) -> list[tuple[int, int]] | None:
    # returns a list of tile coords from start to goal (inclusive), or None
    # if no path exists. start is not included in the returned path (the
    # player is already there); the first entry is the next tile to step to.
    if start == goal:
        return []
    if not world.is_walkable(*goal):
        return None

    open_heap: list[tuple[float, int, tuple[int, int]]] = []
    counter = 0
    heapq.heappush(open_heap, (0.0, counter, start))
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    g_score: dict[tuple[int, int], float] = {start: 0.0}

    while open_heap:
        _, _, current = heapq.heappop(open_heap)
        if current == goal:
            return _reconstruct(came_from, current)
        cx, cy = current
        for dx, dy, step_cost in _NEIGHBORS:
            nx, ny = cx + dx, cy + dy
            if not world.is_walkable(nx, ny):
                continue
            # block diagonal moves that would clip a solid corner: e.g. moving
            # NE through (cx+1, cy) blocked or (cx, cy-1) blocked.
            if dx != 0 and dy != 0:
                if not world.is_walkable(cx + dx, cy) or not world.is_walkable(cx, cy + dy):
                    continue
            tentative_g = g_score[current] + step_cost
            neighbor = (nx, ny)
            if tentative_g < g_score.get(neighbor, float('inf')):
                came_from[neighbor] = current
                g_score[neighbor] = tentative_g
                f = tentative_g + _octile(neighbor, goal)
                counter += 1
                heapq.heappush(open_heap, (f, counter, neighbor))

    return None


def _octile(a: tuple[int, int], b: tuple[int, int]) -> float:
    # admissible heuristic for 8-dir grids: dx + dy + (√2 - 2) * min(dx, dy).
    # equals shortest unobstructed distance with cardinal=1, diag=√2.
    dx = abs(a[0] - b[0])
    dy = abs(a[1] - b[1])
    return (dx + dy) + (_SQRT2 - 2) * min(dx, dy)


def _reconstruct(came_from, end) -> list[tuple[int, int]]:
    path = [end]
    cur = end
    while cur in came_from:
        cur = came_from[cur]
        path.append(cur)
    path.reverse()
    # drop the start tile — the caller is already there.
    return path[1:]
