
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
    surface = pg.display.set_mode((1280, 720))
    try:
        # title <-> world-select loop: the world screen's Back returns here so
        # the player can bounce between them until they pick a world or quit.
        while True:
            choice = show_title(surface)
            if choice is None:
                return
            if choice[0] == 'singleplayer':
                selection = show_world_select(surface)
                if selection is None:
                    continue  # Back: return to the title screen
                save_path, world_name = selection
                from game import Game
                Game(save_path=save_path, world_name=world_name).start()
                return  # game exited -> quit the launcher
            else:
                _, host, port = choice
                from client import run as run_client
                run_client(host, port)
                return
    finally:
        pg.quit()


if __name__ == '__main__':
    main()
