
# headless per-player item storage: a slots list + add_item, no UI.
#
# the player's authoritative inventory lives here, on the entity's 'player'
# component, so it's per-player and constructs with no display (the server
# needs that). the Inventory class in inventory.py is the client-side *view*
# that renders/edits the local player's PlayerInventory.

from config import INVENTORY_SLOTS
import slots as slot_ops


class PlayerInventory:
    def __init__(self) -> None:
        self.slots: list[dict | None] = [None] * INVENTORY_SLOTS

    def add_item(self, item_id: str, quantity: int) -> int:
        # shared slot logic — returns leftover that didn't fit (>0 only when
        # all slots are taken by mismatched items).
        return slot_ops.add(self.slots, item_id, quantity)
