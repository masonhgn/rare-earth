
# entry point / launcher. shows the title screen, then runs the chosen mode in
# the same window (single player = local Game; multiplayer = networked client).
#
# the server is its own process:  python src/server.py

import pygame as pg

from titlescreen import show_title


def main() -> None:
    pg.init()
    pg.font.init()
    pg.display.set_caption('rare-earth')
    surface = pg.display.set_mode((1280, 720))
    try:
        choice = show_title(surface)
        if choice is None:
            return
        if choice[0] == 'singleplayer':
            from game import Game
            Game().start()
        else:
            _, host, port = choice
            from client import run as run_client
            run_client(host, port)
    finally:
        pg.quit()


if __name__ == '__main__':
    main()
