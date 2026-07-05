# Tech-debt backlog

Deferred code-health items from the 2026-07 fragility review. Items 1–5 from
that review are being addressed directly; the two below are parked for later.

## 6. Panels mix rendering with three different mutation routes

`ExchangePanel` mutates game state via three different seams: a market object
(spot buy/sell), module functions (`contracts.accept_contract` / `cancel`), and
a grid widget (`SlotGrid.handle_click` for the drop box). That inconsistency is
why networking the panel needed three separate intent-hooks (`on_accept`,
`on_cancel`, `on_dropbox_click`).

**Fix idea:** a unified "panel emits an action; the host applies it" seam — the
panel returns a description of what the click should do, and the SP host applies
it directly while the net client turns it into an intent. Makes future panels
net-ready for free and removes the SP/MP branching from inside the panels.

## 7. Untyped bare dicts for items

The held cursor is `{item_id, quantity, screen_pos}` and inventory/slot entries
are `{item_id, quantity}`; the transient `screen_pos` key is stripped on some
paths (`input_handler._held_payload`) but not others. There's no schema, so a
missing or mistyped key fails at runtime, far from the cause.

**Fix idea:** a small typed wrapper (dataclass) or, more cheaply, shared key
constants plus `held_payload(...)` / `slot(...)` constructor helpers so the
shape is defined in one place.
