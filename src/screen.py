
from spritesheet import SpriteSheet
import pygame as pg

from config import SCREEN_WIDTH, SCREEN_HEIGHT, SPRITE_SHEET_FILE, SPRITE_SHEET_ID_FILE, TILE_LENGTH





class Screen:
    def __init__(self):

        self.surface = pg.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
        self.sprite_sheet = SpriteSheet(SPRITE_SHEET_FILE, \
            SPRITE_SHEET_ID_FILE, TILE_LENGTH)
        self.sprite_dict = self.sprite_sheet.load_sprites()
        self.view_center = (0,0)


    def _world_to_screen(self, wx, wy):
        return (wx - self.view_center[0] + SCREEN_WIDTH/2, \
                wy - self.view_center[1] + SCREEN_HEIGHT/2)



    def render(self, grid, x,y, view_center):
        self.view_center = view_center
        '''this arranges 1+ tiles at and blits them to the screen'''
        #build sequence array
        sequence = []

        for tile_row in range(len(grid)):
            for tile_col in range(len(grid[0])):
                world_x, world_y = x + tile_col * TILE_LENGTH, y + tile_row * TILE_LENGTH
                source = self.sprite_dict[grid[tile_row][tile_col]] #load sprite
                dest = self._world_to_screen(world_x, world_y)
                sequence.append((source, dest))
        self.surface.blits(sequence)
