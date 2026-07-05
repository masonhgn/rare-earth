
# entry point / launcher. shows the title screen, then runs the chosen mode in
# the same window (single player = local Game; multiplayer = networked client).
#
# the server is its own process:  python src/server.py

import pygame as pg

from titlescreen import show_title
from worldselect import show_world_select


def main() -> None:
    pg.init()
    pg.font.init()
    pg.display.set_caption('rare-earth')
    try:
        # title <-> world-select <-> game loop. the world screen's Back and a
        # game's "Back to Title" both return here so the player can bounce
        # between screens until they pick Quit.
        while True:
            # (re)acquire the window each pass — a game/client may have reopened
            # the display at a different size, invalidating the old surface.
            surface = pg.display.set_mode((1280, 720))
            choice = show_title(surface)
            if choice is None:
                return
            if choice[0] == 'singleplayer':
                selection = show_world_select(surface)
                if selection is None:
                    continue  # Back: return to the title screen
                save_path, world_name = selection
                from game import Game
                if Game(save_path=save_path, world_name=world_name).start() == 'title':
                    continue
                return  # Quit Game -> quit the launcher
            else:
                _, host, port = choice
                from client import run as run_client
                if run_client(host, port) == 'title':
                    continue
                return
    finally:
        pg.quit()


if __name__ == '__main__':
    main()
