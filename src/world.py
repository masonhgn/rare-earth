
import random
from entity import Entity
from config import *
from player import Player







class World:
    def __init__(self):
        self.map_grid = None
        self.generate_random_map(['grass'], 30,30)

        self.entities = {}
        self.spawn_player()


    def generate_random_map(self, tile_ids, width, height):

        result = []

        for i in range(height):
            row = []
            for j in range(width):
                row.append(random.choice(tile_ids))

            result.append(row)

        self.width, self.height = width, height
        self.map_grid = result

    def load_map_file(self, file_name: str):
        grid = []
        with open(file_name, 'r') as file:
            for line in file:
                row = line.strip().split()
                row = [int(x) for x in row]
                grid.append(row)
        self.map_grid = grid

    def add_entity(self, entity):
        self.entities[entity.id] = entity


    def spawn_player(self):
        player = Player(PLAYER_SPAWN)
        self.add_entity(player)


    def get_player(self):
        return self.entities['player']



    def get_entity_rendering_order(self):
        '''return a list which we will use to render each entity so
        that we render the map first, then everything else, then player etc.'''
        result = []


        

    



    
        
