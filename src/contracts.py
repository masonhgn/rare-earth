
# forward contracts: per-exchange board of "you-give-A-for-B" offers.
# every contract is daily — accept now, deliver before tomorrow's day
# rollover or forfeit collateral. resolution is batch-fired by
# ContractSystem.settle_day_rollover when the day clock advances.
#
# data shape (a contract is a plain dict so it round-trips through json
# trivially):
#   {
#     id: uuid str,
#     deliver_item: item_id (can be 'coin'),
#     deliver_qty: int,
#     receive_item: item_id (can be 'coin'),
#     receive_qty: int,
#     collateral: int (always in coin),
#     due_day: int (the day the contract settles on; set at accept time)
#   }
#
# board contracts have no due_day until accept_contract is called —
# they're stamped with current_day + 1 at that moment so the deadline
# is always "tomorrow" from the player's perspective.
#
# resolution outcomes (handled by ContractSystem.settle_day_rollover):
#   - deliver in time   : drop box auto-pulls deliver_qty of deliver_item;
#                         player receives receive_qty of receive_item +
#                         their collateral back.
#   - day passes        : collateral is forfeit, contract is voided.
#                         deposited items stay in the drop box.
#   - cancel            : no payout; collateral returned. deposited
#                         items stay in the drop box.
#
# pricing: receive_qty is computed at generation time from the *current*
# spot price plus a small margin (1.0–1.3x). once a contract is accepted
# its rate is frozen — the field literally doesn't update again, so spot
# can drift after that without changing the trade economics.

import random
import uuid

import slots as slot_ops


# board capacity. accepting a slot leaves it None (per design: no
# refill). regenerated only at fresh world boot.
BOARD_SIZE = 6

# how much the player owes at one side of the contract. small enough
# that early-game inventories can satisfy at least the smaller ones.
QTY_RANGE = (5, 30)

# margin multiplier applied to spot when sizing the receive_qty (or the
# coin demand for inverse contracts). 1.0 = at-spot, >1.0 favours player
# on resource-out / counterparty on resource-in.
MARGIN_RANGE = (1.0, 1.3)

# collateral = COLLATERAL_RATIO * value-of-what-player-is-providing,
# at the contract's locked rate. losing collateral on failure costs the
# player more than just walking away from spot.
COLLATERAL_RATIO = 1.5

# fraction of generated contracts that are "deliver resource, receive
# coin" (the natural game-loop direction). remainder are coin->resource.
RESOURCE_OUT_PROB = 0.7


def generate_contract(spot_market) -> dict:
    # pick a direction first, then size both sides accordingly.
    tradeable = spot_market.tradeable_ids()
    if not tradeable:
        raise RuntimeError('contract generator called with no tradeable items')
    qty = random.randint(*QTY_RANGE)
    margin = random.uniform(*MARGIN_RANGE)
    cid = str(uuid.uuid4())

    if random.random() < RESOURCE_OUT_PROB:
        # deliver resource, receive coin. natural mining-game flow.
        deliver_item = random.choice(tradeable)
        spot = spot_market.price(deliver_item) or 1
        receive_qty = max(1, int(qty * spot * margin))
        # value of provided resource (at contract rate) = receive_qty
        collateral = max(1, int(receive_qty * COLLATERAL_RATIO))
        return {
            'id': cid,
            'deliver_item': deliver_item,
            'deliver_qty': qty,
            'receive_item': 'coin',
            'receive_qty': receive_qty,
            'collateral': collateral,
            # due_day stamped at accept time, not now — board contracts
            # don't tick toward expiry until the player commits.
            'due_day': None,
        }
    else:
        # deliver coin, receive resource. player pre-pays for a future
        # supply; counterparty charges a premium for the lock.
        receive_item = random.choice(tradeable)
        spot = spot_market.price(receive_item) or 1
        deliver_qty = max(1, int(qty * spot * margin))
        collateral = max(1, int(deliver_qty * COLLATERAL_RATIO))
        return {
            'id': cid,
            'deliver_item': 'coin',
            'deliver_qty': deliver_qty,
            'receive_item': receive_item,
            'receive_qty': qty,
            'collateral': collateral,
            'due_day': None,
        }


def initial_board(spot_market) -> list:
    return [generate_contract(spot_market) for _ in range(BOARD_SIZE)]


def ensure_board(es: dict, spot_market) -> None:
    # fill a player's contract board on first use (fresh spawn / new world).
    # no-op once populated, so a loaded save keeps whatever board it had.
    if es is not None and not es['board']:
        es['board'] = initial_board(spot_market)


# ---------------------------------------------------------------------------
# ContractSystem — per-frame countdown + auto-settle on expiry
# ---------------------------------------------------------------------------

class ContractSystem:
    # ticks every active contract's countdown each frame. when a
    # countdown reaches 0 the system attempts settlement: pull the
    # required deliver items from the exchange's drop box; if they're
    # all present, complete the trade; otherwise the player forfeits
    # the collateral.

    def __init__(self, world) -> None:
        # resolves the recipient player at settle time (per-player contract
        # ownership lands in Phase 3); no captured inventory, so it works for
        # both the client and a headless server.
        self.world = world

    def settle_day_rollover(self, current_day: int) -> None:
        # called on day rollover. each player's own active contracts settle
        # against their own drop box + inventory; not-yet-due contracts carry
        # over. per-player ownership: your collateral, your box, your payout.
        for player in self.world.players():
            self._settle_due(player.exchange_state, current_day, player)

    def _settle_due(self, es: dict, current_day: int, player) -> None:
        if es is None:
            return
        survivors = []
        for contract in es['active']:
            due = contract.get('due_day')
            if due is None or due > current_day:
                survivors.append(contract)
                continue
            self._settle(contract, es, player)
        es['active'] = survivors

    def _settle(self, contract: dict, es: dict, player) -> None:
        # try to take deliver_qty of deliver_item from the drop box.
        # all-or-nothing: if we can't fulfil the full quantity, the
        # contract fails and collateral is lost.
        if self._take_from_dropbox(es['drop_box'], contract['deliver_item'], contract['deliver_qty']):
            # success: pay the player + return collateral (both go to
            # inventory; overflow to floor via spawn_dropped_item at the
            # player's feet so nothing is silently lost).
            self._pay_player(player, contract['receive_item'], contract['receive_qty'])
            self._pay_player(player, 'coin', contract['collateral'])
        # failure case: collateral was already deducted at accept time.
        # we don't refund. deposited items remain in the drop box.

    # --- helpers ---

    def _take_from_dropbox(self, drop_box: list, item_id: str, qty: int) -> bool:
        # all-or-nothing pull. shared slot_ops.take handles the two-pass
        # availability check, so a contract that can't actually settle
        # leaves the box untouched.
        return slot_ops.take(drop_box, item_id, qty)

    def _pay_player(self, player, item_id: str, qty: int) -> None:
        if qty <= 0:
            return
        leftover = player.inventory.add_item(item_id, qty)
        if leftover > 0:
            # inventory rejected the full add (mismatched stacks fill it).
            # spit the rest at the player's feet so the payout isn't lost.
            from config import TILE_LENGTH
            sw, sh = player.prototype.sprite_size or (TILE_LENGTH, TILE_LENGTH)
            cx = player.world_x + sw / 2
            cy = player.world_y + sh / 2
            self.world.spawn_dropped_item(item_id, leftover, (cx, cy))


def accept_contract(es: dict, board_index: int, inventory, current_day: int) -> bool:
    # remove the contract from the board (slot becomes None — board
    # does not refill), deduct collateral from coin stacks, append a
    # fresh dict to the active list stamped with `due_day = current + 1`
    # so it settles at tomorrow's day rollover. returns False (and
    # changes nothing) if the player can't afford collateral.
    if board_index < 0 or board_index >= len(es['board']):
        return False
    contract = es['board'][board_index]
    if contract is None:
        return False
    if not slot_ops.spend_coin(inventory, contract['collateral']):
        return False
    es['board'][board_index] = None
    # shallow copy so the active list owns its own dict — otherwise any
    # later mutation of the board entry would leak into the active one.
    active = dict(contract)
    active['due_day'] = current_day + 1
    es['active'].append(active)
    return True


def cancel_contract(es: dict, active_index: int, inventory) -> bool:
    if active_index < 0 or active_index >= len(es['active']):
        return False
    contract = es['active'].pop(active_index)
    # collateral returned, no payout. deposited items in the drop box
    # are NOT auto-refunded; the player retrieves them manually via the
    # drop box ui.
    inventory.add_item('coin', contract['collateral'])
    return True
