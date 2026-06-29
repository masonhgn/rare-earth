
# title screen / main menu: choose Single Player or Multiplayer (with an
# editable server address), or Quit.
#
# self-contained: renders to a surface main.py passes in and returns the choice
# as one of:
#   ('singleplayer',)
#   ('multiplayer', host, port)
#   None                         -> quit
# it does NOT pg.init()/quit() — main.py owns the window so the chosen mode can
# reuse it (no flicker).

import pygame as pg

from ui_theme import get_font

_BG = (24, 28, 34)
_PANEL = (40, 46, 54)
_PANEL_HOVER = (60, 70, 82)
_BORDER = (90, 100, 115)
_TEXT = (235, 235, 235)
_ACCENT = (235, 200, 120)
_FIELD = (18, 20, 24)
_MUTED = (150, 158, 168)


def _button(surface, rect, label, font, hover) -> None:
    pg.draw.rect(surface, _PANEL_HOVER if hover else _PANEL, rect, border_radius=6)
    pg.draw.rect(surface, _BORDER, rect, width=2, border_radius=6)
    lab = font.render(label, True, _TEXT)
    surface.blit(lab, lab.get_rect(center=rect.center))


def _parse_mp(address: str):
    address = address.strip()
    host, sep, port = address.partition(':')
    host = host.strip() or '127.0.0.1'
    if sep:
        try:
            return ('multiplayer', host, int(port))
        except ValueError:
            pass
    return ('multiplayer', host, 5555)


def show_title(surface) -> tuple | None:
    clock = pg.time.Clock()
    w, h = surface.get_size()
    title_font = get_font(72)
    btn_font = get_font(30)
    small_font = get_font(18)

    cx = w // 2
    bw, bh = 320, 56
    bx = cx - bw // 2
    sp_rect = pg.Rect(bx, 286, bw, bh)
    mp_rect = pg.Rect(bx, 358, bw, bh)
    field_rect = pg.Rect(bx, 432, bw, 38)
    quit_rect = pg.Rect(bx, 498, bw, bh)

    address = '127.0.0.1:5555'
    field_active = False

    while True:
        clock.tick(60)
        mouse = pg.mouse.get_pos()
        for event in pg.event.get():
            if event.type == pg.QUIT:
                return None
            elif event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
                field_active = field_rect.collidepoint(mouse)
                if sp_rect.collidepoint(mouse):
                    return ('singleplayer',)
                if mp_rect.collidepoint(mouse):
                    return _parse_mp(address)
                if quit_rect.collidepoint(mouse):
                    return None
            elif event.type == pg.KEYDOWN:
                if field_active:
                    if event.key == pg.K_BACKSPACE:
                        address = address[:-1]
                    elif event.key in (pg.K_RETURN, pg.K_KP_ENTER):
                        field_active = False
                    elif event.unicode and event.unicode.isprintable():
                        address += event.unicode
                elif event.key == pg.K_ESCAPE:
                    return None
                elif event.key in (pg.K_RETURN, pg.K_KP_ENTER):
                    return ('singleplayer',)   # Enter = quick-start single player

        surface.fill(_BG)
        title = title_font.render('rare-earth', True, _ACCENT)
        surface.blit(title, title.get_rect(center=(cx, 160)))

        _button(surface, sp_rect, 'Single Player', btn_font, sp_rect.collidepoint(mouse))
        _button(surface, mp_rect, 'Multiplayer', btn_font, mp_rect.collidepoint(mouse))

        # editable server address (used by Multiplayer)
        srv_lbl = small_font.render('Server address', True, _MUTED)
        surface.blit(srv_lbl, (field_rect.x, field_rect.y - 22))
        pg.draw.rect(surface, _FIELD, field_rect, border_radius=4)
        pg.draw.rect(surface, _ACCENT if field_active else _BORDER, field_rect, width=2, border_radius=4)
        shown = address + ('_' if field_active else '')
        addr = small_font.render(shown, True, _TEXT)
        surface.blit(addr, addr.get_rect(midleft=(field_rect.x + 10, field_rect.centery)))

        _button(surface, quit_rect, 'Quit', btn_font, quit_rect.collidepoint(mouse))

        hint = small_font.render(
            'Multiplayer needs a server running:  python src/server.py', True, _MUTED)
        surface.blit(hint, hint.get_rect(center=(cx, h - 36)))

        pg.display.flip()
