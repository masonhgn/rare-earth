import json



MAP_FILE = 'map/map1.txt'

SCREEN_WIDTH, SCREEN_HEIGHT = 1280, 720
SPRITE_SHEET_FILE = 'sprites/default.png'
SPRITE_SHEET_ID_FILE = 'sprites/sprite_ids.json'
TILE_LENGTH = 64 #game tiles are 64 pixels * 64 pixels


PLAYER_SPAWN = (200,200)
PLAYER_TILES = [['player']]


def load_entity_metadata(file_name):
    with open(file_name, 'r') as file:
        payload = json.load(file)
    return payload




ENTITY_METADATA = load_entity_metadata('entities.json')

