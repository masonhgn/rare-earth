
# title screen / main menu: choose Single Player or Multiplayer, or Quit.
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
_MUTED = (150, 158, 168)

# fixed server the Multiplayer button connects to (no in-game address box).
_DEFAULT_HOST = '167.99.234.25'
_DEFAULT_PORT = 5555


def _button(surface, rect, label, font, hover) -> None:
    pg.draw.rect(surface, _PANEL_HOVER if hover else _PANEL, rect, border_radius=6)
    pg.draw.rect(surface, _BORDER, rect, width=2, border_radius=6)
    lab = font.render(label, True, _TEXT)
    surface.blit(lab, lab.get_rect(center=rect.center))


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
    quit_rect = pg.Rect(bx, 470, bw, bh)

    mp_choice = ('multiplayer', _DEFAULT_HOST, _DEFAULT_PORT)

    while True:
        clock.tick(60)
        mouse = pg.mouse.get_pos()
        for event in pg.event.get():
            if event.type == pg.QUIT:
                return None
            elif event.type == pg.MOUSEBUTTONDOWN and event.button == 1:
                if sp_rect.collidepoint(mouse):
                    return ('singleplayer',)
                if mp_rect.collidepoint(mouse):
                    return mp_choice
                if quit_rect.collidepoint(mouse):
                    return None
            elif event.type == pg.KEYDOWN:
                if event.key == pg.K_ESCAPE:
                    return None
                elif event.key in (pg.K_RETURN, pg.K_KP_ENTER):
                    return ('singleplayer',)   # Enter = quick-start single player

        surface.fill(_BG)
        title = title_font.render('rare-earth', True, _ACCENT)
        surface.blit(title, title.get_rect(center=(cx, 160)))

        _button(surface, sp_rect, 'Single Player', btn_font, sp_rect.collidepoint(mouse))
        _button(surface, mp_rect, 'Multiplayer', btn_font, mp_rect.collidepoint(mouse))

        # static server address the Multiplayer button uses (display-only).
        srv_lbl = small_font.render(f'Server: {_DEFAULT_HOST}', True, _MUTED)
        surface.blit(srv_lbl, srv_lbl.get_rect(center=(cx, mp_rect.bottom + 24)))

        _button(surface, quit_rect, 'Quit', btn_font, quit_rect.collidepoint(mouse))

        pg.display.flip()
