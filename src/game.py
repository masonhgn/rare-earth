from world import World
from screen import Screen
import pygame as pg







class Game:
    def __init__(self):
        pg.init()

        self.world = World()
        self.screen = Screen()

        
        self.clock = pg.time.Clock()
        self.dt = 0
        self.running = False



    def start(self):
        if self.running:
            print('game already started...')
            return
    
        self.running = True
        player = self.world.get_player()

        while self.running:
            #player input
            self.handle_controls()

            # fill the screen with a color to wipe away anything from last frame
            self.screen.surface.fill('red')

            vc = (player.world_x, player.world_y)
            
            #render world
            self.screen.render(self.world.map_grid, 0,0,vc)

            self.screen.render(player.prototype.grid, player.world_x, player.world_y, vc)

            # flip() the display to put your work on screen
            pg.display.flip()

            # limits FPS to 60
            # dt is delta time in seconds since last frame, used for framerate-
            # independent physics.
            self.dt = self.clock.tick(60) / 1000
            

        



    def stop(self):
        if not self.running:
            print('game already stopped...')
            return
        self.running = False
        pg.quit()





    def handle_controls(self):

        # poll for events
        # pygame.QUIT event means the user clicked X to close your window

        for event in pg.event.get():
            if event.type == pg.QUIT:
                self.running = False


        player = self.world.get_player()
        speed = player.prototype.speed * self.dt

        keys = pg.key.get_pressed()
        if keys[pg.K_w]:
            #move up
            player.move_continuous(0,-speed)
        if keys[pg.K_s]:
            #move down
            player.move_continuous(0,speed)
        if keys[pg.K_a]:
            #move left
            player.move_continuous(-speed,0)
        if keys[pg.K_d]:
            #move right
            player.move_continuous(speed,0)

