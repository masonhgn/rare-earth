
# entry point / launcher. shows the title screen, then runs the chosen mode in
# the same window. both modes run the same Client loop: single-player over an
# in-process LocalTransport (a listen server), multiplayer over a socket.
#
# the dedicated multiplayer server is its own process:  python src/server.py

import pygame as pg

import respath
respath.init()   # frozen exe: chdir into the bundled assets before anything loads

from config import SCREEN_WIDTH, SCREEN_HEIGHT
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
            surface = pg.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
            # start every screen with key-repeat off; text fields turn it on only
            # while focused, but a screen exited without blurring could leave it
            # set and make gameplay KEYDOWNs machine-gun.
            pg.key.set_repeat()
            choice = show_title(surface)
            if choice is None:
                return
            if choice[0] == 'singleplayer':
                selection = show_world_select(surface)
                if selection is None:
                    continue  # Back: return to the title screen
                save_path, world_name = selection
                from client import run_local
                if run_local(save_path, world_name) == 'title':
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
