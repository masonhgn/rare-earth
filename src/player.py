from entity import Entity


class Player(Entity):
    def __init__(self, world_pos):
        super().__init__(world_pos, [['player','player',],['player','player']], False, 'player')
        self.walk_speed = 200

