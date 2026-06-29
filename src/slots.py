
# pure functions over slot lists.
#
# a "slots" list is the canonical inventory shape used everywhere:
# list[dict|None] where each dict is {'item_id': str, 'quantity': int}.
# the player's inventory, factory machine input/output, exchange drop
# box, and contract drop box pulls all share this shape, so the slot
# operations are decoupled from any class — the caller passes the list
# in and the functions read/mutate it in place.
#
# stack_limit semantics: stacks are unlimited by design. add() always
# fits the whole qty into an existing matching stack or first empty
# slot; only returns leftover when all slots are taken by mismatched
# items.

def count(slots: list, item_id: str) -> int:
    # total quantity of `item_id` across all slots.
    return sum(
        s['quantity'] for s in slots
        if s and s['item_id'] == item_id
    )


def can_add(slots: list, item_id: str) -> bool:
    # would add(slots, item_id, n) succeed for some n>0? true if any
    # matching stack or empty slot exists.
    for s in slots:
        if s is None or s['item_id'] == item_id:
            return True
    return False


def add(slots: list, item_id: str, qty: int) -> int:
    # deposit qty into the first matching stack, else first empty slot.
    # returns the leftover that didn't fit (0 unless slots are full of
    # mismatched items).
    if qty <= 0:
        return 0
    for s in slots:
        if s and s['item_id'] == item_id:
            s['quantity'] += qty
            return 0
    for i, s in enumerate(slots):
        if s is None:
            slots[i] = {'item_id': item_id, 'quantity': qty}
            return 0
    return qty


def can_add_all(slots: list, items: list) -> bool:
    # multi-item fit check: would adding every (item_id, qty) in `items`
    # succeed in any order? unlimited stacks means each distinct item id
    # needs at most one slot (existing matching stack or one empty), so
    # we just count how many new ids need a fresh slot and compare to
    # empties. used for factory recipe output deposits.
    existing_ids = {s['item_id'] for s in slots if s}
    available_empty = sum(1 for s in slots if s is None)
    extra_needed = 0
    for item_id, _ in items:
        if item_id not in existing_ids:
            extra_needed += 1
            existing_ids.add(item_id)
    return extra_needed <= available_empty


def take(slots: list, item_id: str, qty: int) -> bool:
    # remove qty of item_id, draining matching stacks in order until
    # satisfied. all-or-nothing: returns False (and mutates nothing) if
    # the total available is less than qty.
    if qty <= 0:
        return True
    if count(slots, item_id) < qty:
        return False
    remaining = qty
    for i, s in enumerate(slots):
        if remaining <= 0:
            break
        if s and s['item_id'] == item_id:
            taken = min(s['quantity'], remaining)
            s['quantity'] -= taken
            remaining -= taken
            if s['quantity'] == 0:
                slots[i] = None
    return True


# --- coin convenience shims ---
#
# coin is just an item id, but every panel was reaching for it by name
# enough times that having dedicated helpers keeps the call sites
# readable. they're trivial passthroughs over count/take.

COIN = 'coin'


def click(slots: list, idx, held: dict | None, take_only: bool = False) -> dict | None:
    # pick / place / merge / swap for one slot click. mutates `slots` in place,
    # returns the new held stack. mirrors ui.SlotGrid.handle_click so the
    # authoritative server can run inventory drag-drop without the UI widget.
    if idx is None or idx < 0 or idx >= len(slots):
        return held
    if take_only and held is not None:
        return held
    slot = slots[idx]
    if held is None:
        if slot is None:
            return None
        slots[idx] = None
        return slot
    if slot is None or slot['item_id'] == held['item_id']:
        if slot is None:
            slots[idx] = {'item_id': held['item_id'], 'quantity': held['quantity']}
        else:
            slot['quantity'] += held['quantity']
        return None
    new_held = slot
    slots[idx] = {'item_id': held['item_id'], 'quantity': held['quantity']}
    return new_held


def coin_count(inventory) -> int:
    return count(inventory.slots, COIN)


def spend_coin(inventory, amount: int) -> bool:
    # returns True if the spend succeeded. caller is responsible for
    # the affordability decision (we still gate on `take` though so a
    # missed check can't drive coin negative).
    return take(inventory.slots, COIN, amount)
