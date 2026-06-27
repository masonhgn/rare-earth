
# spot market: per-item global price that walks every 5s with
# mean-reverting drift.
#
# items are tradeable iff their json has a `spot_price` field. that field
# doubles as both the initial price AND the mean-reversion target the
# random walk drifts toward. items without it are not listed on spot and
# can't be sold or bought there.
#
# each tick picks a step from {-2,-1,0,+1,+2}. when at target, picks are
# uniform. when off-target, weights tilt toward target-closing steps so
# prices come home on their own — that's the "soft ceiling" answer in
# the design phase: there's no hard cap, but a wandering price gets
# pulled back over time.
#
# hard floor at 0. no ceiling — mean reversion handles it.

import os
import random

from config import ITEMS_DIR
from item import load_item
import slots as slot_ops


# how often the walk takes a step. real seconds, decoupled from the
# day clock so price action is visible while a player just stands there.
SPOT_TICK_SEC = 5.0

# step magnitudes the walk picks from each tick.
STEP_CHOICES = (-2, -1, 0, 1, 2)

# bid/ask spread. the stored price is the mid; the player buys above it
# and sells below it. half_spread = max(1, round(mid * SPREAD_FRACTION)),
# so even a 2-coin item has a real 1-coin-each-way cost. the spread is
# what makes spot churn lossy and keeps forward contracts (priced at mid
# plus their own margin) worth using.
SPREAD_FRACTION = 0.06

# how many price points the per-item history keeps for the sparkline. at
# one point per SPOT_TICK_SEC, 64 points is ~5 minutes of recent action.
HISTORY_LEN = 64


def _half_spread(mid: int) -> int:
    return max(1, round(mid * SPREAD_FRACTION))


def _drift_weights(diff: int) -> list[float]:
    # diff > 0  -> current is below target, bias positive steps
    # diff < 0  -> current is above target, bias negative steps
    # diff == 0 -> uniform
    # pressure capped so a wildly off price doesn't always pick +2/-2
    pressure = min(2.0, abs(diff) * 0.3)
    weights: list[float] = []
    for s in STEP_CHOICES:
        w = 1.0
        if (diff > 0 and s > 0) or (diff < 0 and s < 0):
            w += pressure * abs(s)
        weights.append(w)
    return weights


def _discover_tradeable() -> dict[str, int]:
    # scan items/*.json once and gather {item_id: target_price} from any
    # item that declares a spot_price field.
    targets: dict[str, int] = {}
    for fn in os.listdir(ITEMS_DIR):
        if not fn.endswith('.json'):
            continue
        item_id = fn[:-5]
        proto = load_item(item_id)
        if proto.spot_price is not None:
            targets[item_id] = proto.spot_price
    return targets


class SpotMarket:
    def __init__(self) -> None:
        self.targets = _discover_tradeable()
        # starting price = target for every tradeable item.
        self.prices: dict[str, int] = dict(self.targets)
        # per-item recent price points for the spot-tab sparkline. seeded
        # with the current price so a fresh market draws a flat line rather
        # than nothing. not persisted — it repopulates one point per tick.
        self.history: dict[str, list[int]] = {}
        self.seed_history()
        # seconds accumulated since the last walk step.
        self._tick_clock = 0.0

    def seed_history(self) -> None:
        # reset each item's history to a single point at its current price.
        # called on construction and again after a save load (which rewrites
        # prices), so the sparkline starts from the restored value.
        self.history = {item_id: [price] for item_id, price in self.prices.items()}

    def tick(self, dt: float) -> None:
        # while-loop instead of single-fire so a large dt (e.g. resuming
        # from a long pause) still resolves the correct number of steps.
        self._tick_clock += dt
        while self._tick_clock >= SPOT_TICK_SEC:
            self._tick_clock -= SPOT_TICK_SEC
            self._step_all()

    def _step_all(self) -> None:
        for item_id, target in self.targets.items():
            current = self.prices[item_id]
            diff = target - current
            weights = _drift_weights(diff)
            step = random.choices(STEP_CHOICES, weights=weights)[0]
            self.prices[item_id] = max(0, current + step)
            hist = self.history.setdefault(item_id, [])
            hist.append(self.prices[item_id])
            if len(hist) > HISTORY_LEN:
                del hist[:-HISTORY_LEN]

    def price(self, item_id: str) -> int | None:
        # mid price. forward contracts size off this; spot trades use the
        # spread-adjusted buy_price / sell_price below.
        return self.prices.get(item_id)

    def buy_price(self, item_id: str) -> int | None:
        mid = self.price(item_id)
        if mid is None:
            return None
        return mid + _half_spread(mid)

    def sell_price(self, item_id: str) -> int | None:
        mid = self.price(item_id)
        if mid is None:
            return None
        return max(0, mid - _half_spread(mid))

    # --- trade actions ---
    #
    # all-or-nothing single-unit transactions at the current price. caller
    # is responsible for triggering them (e.g. on a button click) but
    # doesn't have to reason about coin accounting or inventory capacity.

    def sell(self, inventory, item_id: str, qty: int = 1) -> bool:
        if qty <= 0:
            return False
        price = self.sell_price(item_id)
        if price is None:
            return False
        if not slot_ops.take(inventory.slots, item_id, qty):
            return False
        inventory.add_item('coin', price * qty)
        return True

    def buy(self, inventory, item_id: str, qty: int = 1) -> bool:
        if qty <= 0:
            return False
        price = self.buy_price(item_id)
        if price is None:
            return False
        total = price * qty
        if slot_ops.coin_count(inventory) < total:
            return False
        if not slot_ops.can_add(inventory.slots, item_id):
            return False
        slot_ops.spend_coin(inventory, total)
        inventory.add_item(item_id, qty)
        return True

    # --- trade sizing for the qty selector (x1/x10/x100/all) ---

    def max_sell_qty(self, inventory, item_id: str) -> int:
        # capped by how many units the player actually holds.
        if self.sell_price(item_id) is None:
            return 0
        return slot_ops.count(inventory.slots, item_id)

    def max_buy_qty(self, inventory, item_id: str) -> int:
        # capped by coin affordability at the buy (ask) price. a 0/None
        # price or a full mismatched inventory yields 0.
        price = self.buy_price(item_id)
        if not price:
            return 0
        if not slot_ops.can_add(inventory.slots, item_id):
            return 0
        return slot_ops.coin_count(inventory) // price

    def tradeable_ids(self) -> list[str]:
        # stable order so the spot tab list doesn't shuffle each frame.
        # sorted by id; later we can switch to a curated order via a
        # `spot_sort` field on items if we want to control listing order.
        return sorted(self.targets.keys())
