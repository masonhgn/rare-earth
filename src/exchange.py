
# exchange entity ui: modal panel with three tabs (spot, forward, drop
# box). composed from the ui.py primitives — no per-tab baked image.
#
# spot tab (phase 2): scrollable list of tradeable items with sell/buy
# buttons per row. prices come from a shared SpotMarket on Game; trades
# mutate the player inventory directly (sell deposits coin, buy spends
# coin and adds the item). buy is blocked when the player can't afford
# it; sell is blocked when they don't own the item.
#
# forward + drop box tabs are still placeholders — they land in phases
# 3 and 4.

import pygame as pg

from contracts import accept_contract, cancel_contract
from item import format_quantity, get_item_icon, load_item
from ui import NineSliceSkin, TabStrip, Button, ScrollList, SlotGrid
from ui_theme import (
    COLOR_ACCENT_GOLD, COLOR_ROW_STRIPE, COLOR_TEXT_FAINT,
    COLOR_TEXT_GHOSTED, COLOR_TEXT_PRIMARY,
    MODAL_HEADER_H, MODAL_INNER_MARGIN, MODAL_TAB_GAP, MODAL_TAB_H,
    PANEL_SKIN_CORNER, PANEL_SKIN_FILE, PANEL_SKIN_SCALE, get_font,
)
import slots as slot_ops


PANEL_W, PANEL_H = 1040, 640

# layout constants share defaults with other modals via ui_theme so a
# single tweak there propagates to every panel.
INNER_MARGIN = MODAL_INNER_MARGIN
HEADER_H = MODAL_HEADER_H
TAB_H = MODAL_TAB_H
TAB_GAP = MODAL_TAB_GAP

# spot tab row geometry
SPOT_ROW_H = 56
SPOT_ICON_SIZE = 40
SPOT_BUTTON_W = 96
SPOT_BUTTON_GAP = 8
# scrollbar-padding alias kept for readability inside this module;
# canonical value lives on ScrollList so widgets that don't know about
# exchange layout still get the same number.
SPOT_RIGHT_PAD = ScrollList.SCROLLBAR_PAD

# drop box tab grid geometry. 6x5 = 30 slots, matching the exchange
# entity's drop_box_slots. tweak in tandem with the entity json.
DROPBOX_COLS = 6
DROPBOX_ROWS = 5
DROPBOX_SLOT_SIZE = 52
DROPBOX_SLOT_GAP = 6

# forward tab geometry. rows compressed: just deliver-icon, receive-icon,
# and an Accept/Cancel button — no arrow, no countdown, no collateral
# text. qty rides in the bottom-right corner of each icon like an
# inventory stack label.
CONTRACT_ROW_H = 56
CONTRACT_ICON_SIZE = 40
CONTRACT_BUTTON_W = 96
CONTRACT_COL_GAP = 14   # gap between the two side-by-side lists
CONTRACT_SECTION_HEADER_H = 22

# tab identifiers — kept as constants so we don't typo strings across
# render/click branches.
TAB_SPOT = 'spot'
TAB_FORWARD = 'forward'
TAB_DROPBOX = 'dropbox'
TAB_ORDER = (TAB_SPOT, TAB_FORWARD, TAB_DROPBOX)
TAB_LABELS = ('Spot', 'Forward', 'Drop Box')


class ExchangePanel:
    def __init__(self, spot_market, inventory, day_clock) -> None:
        self.open = False
        self.entity = None
        self.spot_market = spot_market
        self.inventory = inventory
        # day_clock is read at accept-contract time to stamp due_day;
        # the panel itself doesn't tick anything.
        self.day_clock = day_clock
        self.skin = NineSliceSkin(PANEL_SKIN_FILE, PANEL_SKIN_CORNER, scale=PANEL_SKIN_SCALE)
        # smaller fonts than the first pass — the overlay was crowding
        # the border art on a 1280x720 viewport.
        self.font = get_font(20)
        self.font_small = get_font(16)
        self.font_big = get_font(24)
        self.origin: tuple[int, int] = (0, 0)
        self.rect = pg.Rect(0, 0, PANEL_W, PANEL_H)
        self.tabs: TabStrip | None = None
        # spot scroll list. its rect is repositioned each frame based on
        # the panel's current origin; constructed once so scroll offset
        # persists across renders.
        self.spot_list = ScrollList(pg.Rect(0, 0, 0, 0), SPOT_ROW_H)
        # drop box slot grid. rect set per-render once we know the
        # content area, same as spot_list.
        self.drop_grid = SlotGrid(
            pg.Rect(0, 0, 0, 0),
            DROPBOX_COLS, DROPBOX_ROWS, DROPBOX_SLOT_SIZE,
            slot_gap=DROPBOX_SLOT_GAP,
            font=self.font_small,
        )
        # forward tab uses two independent scroll lists — available
        # contracts (the board) and active ones the player has accepted.
        self.forward_board_list = ScrollList(pg.Rect(0, 0, 0, 0), CONTRACT_ROW_H)
        self.forward_active_list = ScrollList(pg.Rect(0, 0, 0, 0), CONTRACT_ROW_H)

    # --- lifecycle ---

    def open_for(self, entity, screen_size: tuple[int, int]) -> None:
        self.entity = entity
        self.open = True
        self._reposition(screen_size)

    def close(self) -> None:
        self.open = False
        self.entity = None

    def _reposition(self, screen_size: tuple[int, int]) -> None:
        x = (screen_size[0] - PANEL_W) // 2
        y = (screen_size[1] - PANEL_H) // 2
        self.origin = (x, y)
        self.rect = pg.Rect(x, y, PANEL_W, PANEL_H)
        prev_active = self.tabs.active if self.tabs is not None else 0
        tab_rect = pg.Rect(
            x + INNER_MARGIN,
            y + INNER_MARGIN + HEADER_H + TAB_GAP,
            PANEL_W - 2 * INNER_MARGIN,
            TAB_H,
        )
        self.tabs = TabStrip(tab_rect, list(TAB_LABELS), self.font, active=prev_active)
        self.spot_list.rect = self._content_rect()

    # --- input ---

    def handle_click(self, mouse_pos: tuple[int, int], held: dict | None) -> dict | None:
        # tab strip click switches the active tab and swallows the event.
        if self.tabs is not None and self.tabs.handle_click(mouse_pos) is not None:
            return held
        active = TAB_ORDER[self.tabs.active] if self.tabs is not None else TAB_SPOT
        if active == TAB_SPOT:
            return self._handle_spot_click(mouse_pos, held)
        if active == TAB_DROPBOX:
            return self._handle_dropbox_click(mouse_pos, held)
        if active == TAB_FORWARD:
            return self._handle_forward_click(mouse_pos, held)
        return held

    def handle_scroll(self, mouse_pos: tuple[int, int], amount: int) -> bool:
        active = TAB_ORDER[self.tabs.active] if self.tabs is not None else TAB_SPOT
        if active == TAB_SPOT and self.spot_list.rect.collidepoint(mouse_pos):
            self.spot_list.handle_scroll(amount)
            return True
        if active == TAB_FORWARD:
            if self.forward_board_list.rect.collidepoint(mouse_pos):
                self.forward_board_list.handle_scroll(amount)
                return True
            if self.forward_active_list.rect.collidepoint(mouse_pos):
                self.forward_active_list.handle_scroll(amount)
                return True
        return False

    def hit(self, mouse_pos: tuple[int, int]) -> bool:
        return self.open and self.rect.collidepoint(mouse_pos)

    # --- render ---

    def render(self, surface: pg.Surface, screen_size: tuple[int, int]) -> None:
        if not self.open or self.entity is None:
            return
        self._reposition(screen_size)
        self.skin.render(surface, self.rect)
        self._render_header(surface)
        self.tabs.render(surface)
        active = TAB_ORDER[self.tabs.active]
        content = self._content_rect()
        if active == TAB_SPOT:
            self._render_spot(surface, content)
        elif active == TAB_FORWARD:
            self._render_forward(surface, content)
        else:
            self._render_dropbox(surface, content)

    def _content_rect(self) -> pg.Rect:
        x, y = self.origin
        top = y + INNER_MARGIN + HEADER_H + TAB_GAP + TAB_H + TAB_GAP
        return pg.Rect(
            x + INNER_MARGIN,
            top,
            PANEL_W - 2 * INNER_MARGIN,
            y + PANEL_H - INNER_MARGIN - top,
        )

    def _render_header(self, surface: pg.Surface) -> None:
        # title only. coin balance lives in the inventory; we don't need
        # to duplicate it in the panel header.
        x, y = self.origin
        title = self.font_big.render('Exchange', True, COLOR_TEXT_PRIMARY)
        surface.blit(title, (x + INNER_MARGIN, y + INNER_MARGIN + 2))

    # --- spot tab ---

    def _render_spot(self, surface: pg.Surface, rect: pg.Rect) -> None:
        self.spot_list.rect = rect
        ids = self.spot_market.tradeable_ids()
        self.spot_list.render(surface, len(ids), lambda s, i, r: self._render_spot_row(s, i, r, ids))

    def _render_spot_row(self, surface: pg.Surface, idx: int, row_rect: pg.Rect, ids: list[str]) -> None:
        item_id = ids[idx]
        proto = load_item(item_id)
        price = self.spot_market.price(item_id)

        # subtle row striping for legibility
        if idx % 2 == 1:
            tint = pg.Surface((row_rect.width - SPOT_RIGHT_PAD, row_rect.height), pg.SRCALPHA)
            tint.fill(COLOR_ROW_STRIPE)
            surface.blit(tint, (row_rect.x, row_rect.y))

        # icon
        icon = get_item_icon(proto, size=SPOT_ICON_SIZE)
        icon_pos = (row_rect.x + 8, row_rect.y + (row_rect.height - icon.get_height()) // 2)
        surface.blit(icon, icon_pos)

        # name
        name_label = self.font.render(proto.name, True, COLOR_TEXT_PRIMARY)
        name_rect = name_label.get_rect(left=row_rect.x + 64, centery=row_rect.centery)
        surface.blit(name_label, name_rect)

        # price (right-of-name, left of the buttons)
        price_label = self.font.render(f'{price} c', True, COLOR_ACCENT_GOLD)
        price_rect = price_label.get_rect(left=row_rect.x + 320, centery=row_rect.centery)
        surface.blit(price_label, price_rect)

        # action buttons. enabled state reflects whether the trade is
        # currently legal — buy needs coin + an open stack, sell needs
        # at least one unit in inventory.
        sell_rect, buy_rect = self._spot_button_rects(row_rect)
        coin = self._coin_count()
        can_buy = price is not None and coin >= price and self._can_add_to_inventory(item_id)
        can_sell = self._inventory_count(item_id) >= 1
        Button(sell_rect, 'Sell 1', self.font, enabled=can_sell).render(surface)
        Button(buy_rect, 'Buy 1', self.font, enabled=can_buy).render(surface)

    def _spot_button_rects(self, row_rect: pg.Rect) -> tuple[pg.Rect, pg.Rect]:
        # two buttons anchored to the right edge, leaving SPOT_RIGHT_PAD
        # for the scrollbar.
        h = row_rect.height - 16
        y = row_rect.y + 8
        right = row_rect.right - SPOT_RIGHT_PAD
        buy = pg.Rect(right - SPOT_BUTTON_W, y, SPOT_BUTTON_W, h)
        sell = pg.Rect(buy.x - SPOT_BUTTON_GAP - SPOT_BUTTON_W, y, SPOT_BUTTON_W, h)
        return sell, buy

    def _handle_spot_click(self, mouse_pos: tuple[int, int], held: dict | None) -> dict | None:
        idx = self.spot_list.row_at_pixel(mouse_pos)
        if idx is None:
            return held
        ids = self.spot_market.tradeable_ids()
        if idx < 0 or idx >= len(ids):
            return held
        # re-derive the visible row rect so the button hit-test matches
        # what the player actually clicked (rows can be scrolled).
        row_y = self.spot_list.rect.y + idx * self.spot_list.row_height - self.spot_list.scroll_offset
        row_rect = pg.Rect(self.spot_list.rect.x, row_y, self.spot_list.rect.width, self.spot_list.row_height)
        sell_rect, buy_rect = self._spot_button_rects(row_rect)
        item_id = ids[idx]
        if sell_rect.collidepoint(mouse_pos):
            self.spot_market.sell(self.inventory, item_id)
        elif buy_rect.collidepoint(mouse_pos):
            self.spot_market.buy(self.inventory, item_id)
        return held

    # --- inventory read shims ---
    #
    # only the read-side helpers stick around — write paths went to
    # SpotMarket.sell/buy and contracts.accept/cancel after the refactor.

    def _coin_count(self) -> int:
        return slot_ops.coin_count(self.inventory)

    def _inventory_count(self, item_id: str) -> int:
        return slot_ops.count(self.inventory.slots, item_id)

    def _can_add_to_inventory(self, item_id: str) -> bool:
        return slot_ops.can_add(self.inventory.slots, item_id)

    # --- forward tab ---

    def _render_forward(self, surface: pg.Surface, rect: pg.Rect) -> None:
        es = self.entity.exchange_state
        board = es['board']
        active = es['active']

        # vertical split: left = available, right = active. column width
        # accounts for a small gap between them.
        col_w = (rect.width - CONTRACT_COL_GAP) // 2
        left_rect = pg.Rect(rect.x, rect.y, col_w, rect.height)
        right_rect = pg.Rect(rect.x + col_w + CONTRACT_COL_GAP, rect.y, col_w, rect.height)

        # available (left)
        avail_label = self.font.render('Available', True, COLOR_TEXT_PRIMARY)
        surface.blit(avail_label, (left_rect.x + 4, left_rect.y + 2))
        self.forward_board_list.rect = pg.Rect(
            left_rect.x, left_rect.y + CONTRACT_SECTION_HEADER_H,
            left_rect.width, left_rect.height - CONTRACT_SECTION_HEADER_H,
        )
        self.forward_board_list.render(
            surface, len(board),
            lambda s, i, r: self._render_board_row(s, i, r, board),
        )

        # active (right)
        active_label = self.font.render('Active', True, COLOR_TEXT_PRIMARY)
        surface.blit(active_label, (right_rect.x + 4, right_rect.y + 2))
        self.forward_active_list.rect = pg.Rect(
            right_rect.x, right_rect.y + CONTRACT_SECTION_HEADER_H,
            right_rect.width, right_rect.height - CONTRACT_SECTION_HEADER_H,
        )
        self.forward_active_list.render(
            surface, max(1, len(active)),
            lambda s, i, r: self._render_active_row(s, i, r, active),
        )

    def _render_board_row(self, surface: pg.Surface, idx: int, row_rect: pg.Rect, board: list) -> None:
        contract = board[idx]
        self._row_stripe(surface, idx, row_rect)
        if contract is None:
            taken = self.font_small.render('(taken)', True, COLOR_TEXT_GHOSTED)
            surface.blit(taken, taken.get_rect(center=row_rect.center))
            return
        layout = self._contract_row_layout(row_rect)
        self._draw_contract_body(surface, contract, layout)
        can_afford = self._coin_count() >= contract['collateral']
        Button(layout['button'], 'Accept', self.font, enabled=can_afford).render(surface)

    def _render_active_row(self, surface: pg.Surface, idx: int, row_rect: pg.Rect, active: list) -> None:
        self._row_stripe(surface, idx, row_rect)
        if idx >= len(active):
            empty = self.font_small.render('No active contracts.', True, COLOR_TEXT_GHOSTED)
            surface.blit(empty, empty.get_rect(center=row_rect.center))
            return
        contract = active[idx]
        layout = self._contract_row_layout(row_rect)
        self._draw_contract_body(surface, contract, layout)
        Button(layout['button'], 'Cancel', self.font).render(surface)

    def _draw_contract_body(self, surface: pg.Surface, contract: dict, layout: dict) -> None:
        # deliver icon + receive icon, each with the qty stamped in the
        # bottom-right corner just like an inventory slot.
        self._draw_item_with_qty(
            surface, layout['deliver_icon'],
            contract['deliver_item'], contract['deliver_qty'],
        )
        self._draw_item_with_qty(
            surface, layout['receive_icon'],
            contract['receive_item'], contract['receive_qty'],
        )

    def _draw_item_with_qty(self, surface: pg.Surface, rect: pg.Rect,
                            item_id: str, qty: int) -> None:
        proto = load_item(item_id)
        icon = get_item_icon(proto, size=rect.width)
        surface.blit(icon, rect.topleft)
        if qty > 1:
            label = self.font_small.render(format_quantity(qty), True, (255, 255, 255))
            shadow = self.font_small.render(format_quantity(qty), True, (0, 0, 0))
            label_rect = label.get_rect(bottomright=(rect.right - 2, rect.bottom - 2))
            surface.blit(shadow, label_rect.move(1, 1))
            surface.blit(label, label_rect)

    def _contract_row_layout(self, row_rect: pg.Rect) -> dict:
        # tight layout: deliver-icon, small gap, receive-icon, then the
        # Accept/Cancel button anchored to the right (leaving scrollbar
        # padding).
        h = row_rect.height
        button = pg.Rect(
            row_rect.right - SPOT_RIGHT_PAD - CONTRACT_BUTTON_W,
            row_rect.y + 8,
            CONTRACT_BUTTON_W, h - 16,
        )
        icon_y = row_rect.y + (h - CONTRACT_ICON_SIZE) // 2
        deliver_icon = pg.Rect(row_rect.x + 8, icon_y, CONTRACT_ICON_SIZE, CONTRACT_ICON_SIZE)
        receive_icon = pg.Rect(
            deliver_icon.right + 12, icon_y, CONTRACT_ICON_SIZE, CONTRACT_ICON_SIZE,
        )
        return {
            'deliver_icon': deliver_icon,
            'receive_icon': receive_icon,
            'button': button,
        }

    def _row_stripe(self, surface: pg.Surface, idx: int, row_rect: pg.Rect) -> None:
        if idx % 2 != 1:
            return
        tint = pg.Surface((row_rect.width - SPOT_RIGHT_PAD, row_rect.height), pg.SRCALPHA)
        tint.fill(COLOR_ROW_STRIPE)
        surface.blit(tint, (row_rect.x, row_rect.y))

    def _handle_forward_click(self, mouse_pos: tuple[int, int], held: dict | None) -> dict | None:
        es = self.entity.exchange_state
        # board accept (left column)
        if self.forward_board_list.rect.collidepoint(mouse_pos):
            board = es['board']
            idx = self.forward_board_list.row_at_pixel(mouse_pos)
            if idx is None or idx < 0 or idx >= len(board):
                return held
            contract = board[idx]
            if contract is None:
                return held
            row_rect = self._visible_row_rect(self.forward_board_list, idx)
            layout = self._contract_row_layout(row_rect)
            if layout['button'].collidepoint(mouse_pos):
                accept_contract(es, idx, self.inventory, self.day_clock.day)
            return held
        # active cancel (right column)
        if self.forward_active_list.rect.collidepoint(mouse_pos):
            active = es['active']
            idx = self.forward_active_list.row_at_pixel(mouse_pos)
            if idx is None or idx < 0 or idx >= len(active):
                return held
            row_rect = self._visible_row_rect(self.forward_active_list, idx)
            layout = self._contract_row_layout(row_rect)
            if layout['button'].collidepoint(mouse_pos):
                cancel_contract(es, idx, self.inventory)
            return held
        return held

    def _visible_row_rect(self, scroll_list: ScrollList, idx: int) -> pg.Rect:
        # row's on-screen rect accounting for the list's current scroll
        # offset. needed so button hit-tests match what the player sees.
        y = scroll_list.rect.y + idx * scroll_list.row_height - scroll_list.scroll_offset
        return pg.Rect(scroll_list.rect.x, y, scroll_list.rect.width, scroll_list.row_height)

    # --- drop box tab ---

    def _render_dropbox(self, surface: pg.Surface, rect: pg.Rect) -> None:
        # short helper text at top of the content area
        hint = self.font_small.render(
            'Deposit items here. Active contracts pull from this box on expiry.',
            True, COLOR_TEXT_FAINT,
        )
        surface.blit(hint, hint.get_rect(midtop=(rect.centerx, rect.y + 8)))

        # center the grid below the hint
        gw, gh = self.drop_grid.total_size()
        grid_x = rect.x + (rect.width - gw) // 2
        grid_y = rect.y + 40 + (rect.height - 40 - gh) // 2
        self.drop_grid.rect = pg.Rect(grid_x, grid_y, gw, gh)
        slots = self.entity.exchange_state['drop_box']
        self.drop_grid.render(surface, slots)

    def _handle_dropbox_click(self, mouse_pos: tuple[int, int], held: dict | None) -> dict | None:
        idx = self.drop_grid.slot_at_pixel(mouse_pos)
        if idx is None:
            return held
        slots = self.entity.exchange_state['drop_box']
        return self.drop_grid.handle_click(idx, held, slots)
