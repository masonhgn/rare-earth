import pygame as pg
import json




class SpriteSheet:
    def __init__(self, sprite_sheet_file: str, sprite_id_file: str, tile_length: int) -> None:
        self.tile_length = tile_length
        self.sheet = pg.image.load(sprite_sheet_file).convert_alpha()
        with open(sprite_id_file, 'r') as file:
            self.sprite_ids = json.load(file)['sprite_ids']

    def get_tile(self, rect):
        rect = pg.Rect(rect)
        image = pg.Surface(rect.size, pg.SRCALPHA).convert_alpha()
        image.blit(self.sheet, (0,0), rect) #set the image to the rect
        return image
    
    def get_tiles(self, rects):
        return [self.get_tile(r) for r in rects]
    
    def load_sprites(self): 
        '''load map of sprite_id -> image'''
        sprites = {}

        for sprite_id, pos in self.sprite_ids.items():
            y,x = pos[0] * self.tile_length, pos[1] * self.tile_length #index -> pixels
            sprites[sprite_id] = self.get_tile((x,y, self.tile_length, self.tile_length))

        return sprites

