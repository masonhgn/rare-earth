
# single-player world select: pick a saved world to load, delete one, or
# create a new named world. shown by main.py after "Single Player".
#
# self-contained like titlescreen: renders to a surface main.py passes in and
# returns the choice as one of:
#   (save_path, world_name)   -> play this world (load if the file exists,
#                                otherwise a fresh world saved to that path)
#   None                      -> back to the title screen / quit
# it does NOT pg.init()/quit() — main.py owns the window.

import pygame as pg

from ui_theme import get_font
from save_state import list_worlds, world_path, delete_world

_BG = (24, 28, 34)
_PANEL = (40, 46, 54)
_PANEL_HOVER = (60, 70, 82)
_BORDER = (90, 100, 115)
_TEXT = (235, 235, 235)
_ACCENT = (235, 200, 120)
_FIELD = (18, 20, 24)
_MUTED = (150, 158, 168)
_DANGER = (200, 80, 80)
_DANGER_HOVER = (230, 110, 110)

_MAX_NAME = 24
_VISIBLE_ROWS = 5


def _button(surface, rect, label, font, hover, *, color=_PANEL, hover_color=_PANEL_HOVER,
            border=_BORDER, text=_TEXT) -> None:
    pg.draw.rect(surface, hover_color if hover else color, rect, border_radius=6)
    pg.draw.rect(surface, border, rect, width=2, border_radius=6)
    lab = font.render(label, True, text)
    surface.blit(lab, lab.get_rect(center=rect.center))


def show_world_select(surface) -> tuple | None:
    clock = pg.time.Clock()
    w, h = surface.get_size()
    cx = w // 2
    title_font = get_font(56)
    row_font = get_font(28)
    meta_font = get_font(18)
    btn_font = get_font(28)
    small_font = get_font(18)

    worlds = list_worlds()
    scroll = 0                 # index of the first visible row
    mode = 'list'              # 'list' | 'new'
    name = ''                  # new-world name buffer
    pending_delete = None      # path armed for a confirming second delete click

    # layout
    list_x = cx - 300
    list_w = 600
    row_h = 64
    row_gap = 8
    list_top = 150
    new_rect = pg.Rect(cx - 260, h - 96, 240, 54)
    back_rect = pg.Rect(cx + 20, h - 96, 240, 54)
    # new-world mode widgets
    field_rect = pg.Rect(cx - 220, 320, 440, 48)
    create_rect = pg.Rect(cx - 220, 392, 210, 54)
    cancel_rect = pg.Rect(cx + 10, 392, 210, 54)

    def row_rect(i: int) -> pg.Rect:
        return pg.Rect(list_x, list_top + i * (row_h + row_gap), list_w, row_h)

    def max_scroll() -> int:
        return max(0, len(worlds) - _VISIBLE_ROWS)

    while True:
        clock.tick(60)
        mouse = pg.mouse.get_pos()
        for event in pg.event.get():
            if event.type == pg.QUIT:
                return None

            if mode == 'list':
                if event.type == pg.MOUSEWHEEL:
                    scroll = max(0, min(max_scroll(), scroll - event.y))
                elif event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
                    if new_rect.collidepoint(mouse):
                        mode, name, pending_delete = 'new', '', None
                        continue
                    if back_rect.collidepoint(mouse):
                        return None
                    hit_row = False
                    for i in range(_VISIBLE_ROWS):
                        idx = scroll + i
                        if idx >= len(worlds):
                            break
                        rr = row_rect(i)
                        del_rect = pg.Rect(rr.right - 108, rr.y + 14, 92, row_h - 28)
                        world = worlds[idx]
                        if del_rect.collidepoint(mouse):
                            hit_row = True
                            if pending_delete == world['path']:
                                delete_world(world['path'])
                                worlds = list_worlds()
                                scroll = min(scroll, max_scroll())
                                pending_delete = None
                            else:
                                pending_delete = world['path']
                            break
                        if rr.collidepoint(mouse):
                            return (world['path'], world['name'])
                    if not hit_row:
                        pending_delete = None
                elif event.type == pg.KEYDOWN and event.key == pg.K_ESCAPE:
                    return None

            else:  # mode == 'new'
                if event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
                    if create_rect.collidepoint(mouse) and name.strip():
                        return (world_path(name), name.strip())
                    if cancel_rect.collidepoint(mouse):
                        mode = 'list'
                elif event.type == pg.KEYDOWN:
                    if event.key == pg.K_ESCAPE:
                        mode = 'list'
                    elif event.key == pg.K_BACKSPACE:
                        name = name[:-1]
                    elif event.key in (pg.K_RETURN, pg.K_KP_ENTER):
                        if name.strip():
                            return (world_path(name), name.strip())
                    elif event.unicode and event.unicode.isprintable() and len(name) < _MAX_NAME:
                        name += event.unicode

        # --- render ---
        surface.fill(_BG)
        title = title_font.render('Select World', True, _ACCENT)
        surface.blit(title, title.get_rect(center=(cx, 90)))

        if mode == 'list':
            if not worlds:
                empty = row_font.render('No worlds yet.', True, _MUTED)
                surface.blit(empty, empty.get_rect(center=(cx, list_top + 80)))
                hint = small_font.render("Click 'New World' to create one.", True, _MUTED)
                surface.blit(hint, hint.get_rect(center=(cx, list_top + 120)))
            for i in range(_VISIBLE_ROWS):
                idx = scroll + i
                if idx >= len(worlds):
                    break
                world = worlds[idx]
                rr = row_rect(i)
                hover = rr.collidepoint(mouse)
                pg.draw.rect(surface, _PANEL_HOVER if hover else _PANEL, rr, border_radius=6)
                pg.draw.rect(surface, _BORDER, rr, width=2, border_radius=6)
                nm = row_font.render(world['name'], True, _TEXT)
                surface.blit(nm, (rr.x + 18, rr.y + 10))
                meta = meta_font.render(f"Day {world['day']}", True, _MUTED)
                surface.blit(meta, (rr.x + 18, rr.y + 38))
                # delete button (two-click confirm)
                del_rect = pg.Rect(rr.right - 108, rr.y + 14, 92, row_h - 28)
                armed = pending_delete == world['path']
                dhover = del_rect.collidepoint(mouse)
                _button(surface, del_rect, 'Sure?' if armed else 'Delete', small_font, dhover,
                        color=_DANGER if armed else _PANEL,
                        hover_color=_DANGER_HOVER if armed else _PANEL_HOVER,
                        border=_DANGER if armed else _BORDER)

            # scroll affordance
            if scroll > 0:
                up = small_font.render('▲ more', True, _MUTED)
                surface.blit(up, up.get_rect(center=(cx, list_top - 18)))
            if scroll < max_scroll():
                dn = small_font.render('▼ more', True, _MUTED)
                surface.blit(dn, dn.get_rect(center=(cx, list_top + _VISIBLE_ROWS * (row_h + row_gap))))

            _button(surface, new_rect, 'New World', btn_font, new_rect.collidepoint(mouse))
            _button(surface, back_rect, 'Back', btn_font, back_rect.collidepoint(mouse))
        else:
            prompt = row_font.render('World name', True, _TEXT)
            surface.blit(prompt, (field_rect.x, field_rect.y - 34))
            pg.draw.rect(surface, _FIELD, field_rect, border_radius=4)
            pg.draw.rect(surface, _ACCENT, field_rect, width=2, border_radius=4)
            shown = (name or 'New World') + '_'
            txt = row_font.render(shown, True, _TEXT if name else _MUTED)
            surface.blit(txt, txt.get_rect(midleft=(field_rect.x + 12, field_rect.centery)))
            can_create = bool(name.strip())
            _button(surface, create_rect, 'Create', btn_font, create_rect.collidepoint(mouse),
                    color=_PANEL if can_create else (30, 34, 40),
                    text=_TEXT if can_create else _MUTED)
            _button(surface, cancel_rect, 'Cancel', btn_font, cancel_rect.collidepoint(mouse))

        pg.display.flip()
