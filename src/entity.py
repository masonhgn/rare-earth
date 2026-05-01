from config import TILE_LENGTH
import uuid



class Entity:
    def __init__(self, prototype, world_pos, entity_id=None):
        '''
        an Entity is a thing that can be rendered via multiple
        different sprites, arranged in a specific grid.

        this could be a player, whose size is a tile or two, or
        a huge entity that is many different tiles.

        the reason why we build up entities with one or more tiles
        is so we can break/remove/move the entity all at once, but
        still be able to cleanly display it anywhere since it is
        just a composition of tiles.

        For some entities we want to require that it locks
        to a grid of tiles (like placing blocks or trees or something)
        these should move tile by tile. i.e. it cannot be placed in 
        between two tiles.

        for other entities, we don't need it to lock to a grid, it
        should be able to move pixel by pixel. (like players, mobs, etc)
        
        '''

        self.prototype = prototype
        self.id = entity_id if entity_id else uuid.uuid4()
        
        self.world_x, self.world_y = world_pos

        



    def move_continuous(self, x,y):
        if self.prototype.tile_locked: return # this is not the right function
        self.world_x += x
        self.world_y += y

    def move_discrete(self,x,y): #moving by tiles instead of pixels
        if not self.prototype.tile_locked: return
        self.world_x += x * TILE_LENGTH
        self.world_y += y * TILE_LENGTH






