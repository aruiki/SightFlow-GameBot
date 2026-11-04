"""Bush Arena: a tiny top-down survival game made for sightflow demos.

You (blue) collect coins (yellow) and shoot enemies (red) that chase you.
Standing in a bush (green) hides you; enemies inside bushes are hidden too.
Grey walls block movement and bullets (the demo bot deliberately does NOT
detect walls - it relies on stuck detection to get around them).
Touching an enemy costs 1 HP. At 0 HP the GAME OVER banner (purple) appears.

Play it yourself:   python demo/arena.py        WASD move, click shoot, R restart
"""
from __future__ import annotations

import math
import os
import random
from dataclasses import dataclass

W, H = 640, 480
BG = (28, 30, 36)
PLAYER = (40, 120, 255)
ENEMY = (230, 40, 40)
BUSH = (40, 140, 60)
COIN = (255, 210, 0)
GAMEOVER = (150, 30, 150)
WALL = (95, 95, 100)
WHITE = (235, 235, 235)


@dataclass
class Body:
    x: float
    y: float
    r: float
    vx: float = 0.0
    vy: float = 0.0


class Arena:
    PLAYER_SPEED = 170.0
    ENEMY_SPEED = 75.0
    BULLET_SPEED = 420.0
    FIRE_COOLDOWN = 0.3
    MAX_ENEMIES = 4
    SPAWN_EVERY = 2.5
    MAX_HP = 5

    def __init__(self, seed: int = 0):
        self.seed = seed
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self):
        self.rng = random.Random(self.seed)
        self.t = getattr(self, "t", 0.0)
        self.bushes = []
        while len(self.bushes) < 6:
            w, h = self.rng.randint(60, 110), self.rng.randint(50, 90)
            x, y = self.rng.randint(20, W - w - 20), self.rng.randint(40, H - h - 20)
            rect = (x, y, w, h)
            if all(not _overlap(rect, b, 25) for b in self.bushes):
                self.bushes.append(rect)
        self.walls = []
        centre = (W / 2 - 40, H / 2 - 40, 80, 80)
        tries = 0
        while len(self.walls) < 3 and tries < 200:
            tries += 1
            horiz = self.rng.random() < 0.5
            w, h = (self.rng.randint(90, 150), 18) if horiz else (18, self.rng.randint(90, 150))
            x, y = self.rng.randint(30, W - w - 30), self.rng.randint(60, H - h - 30)
            rect = (x, y, w, h)
            if all(not _overlap(rect, b, 20) for b in self.bushes + self.walls + [centre]):
                self.walls.append(rect)
        self.player = Body(W / 2, H / 2, 12)
        self.enemies: list[Body] = []
        self.coins: list[Body] = []
        self.bullets: list[Body] = []
        self.hp = self.MAX_HP
        self.score = 0
        self.kills = 0
        self.coins_taken = 0
        self.alive_time = 0.0
        self.game_over = False
        self.spawn_timer = 1.0
        self.fire_timer = 0.0
        self.hurt_timer = 0.0

    def blocked(self, x: float, y: float, r: float) -> bool:
        return any(wx - r < x < wx + ww + r and wy - r < y < wy + wh + r
                   for wx, wy, ww, wh in self.walls)

    def _move(self, b: Body, dx: float, dy: float):
        nx = min(max(b.x + dx, b.r), W - b.r)
        if not self.blocked(nx, b.y, b.r):
            b.x = nx
        ny = min(max(b.y + dy, b.r), H - b.r)
        if not self.blocked(b.x, ny, b.r):
            b.y = ny

    def in_bush(self, b: Body) -> bool:
        return any(x <= b.x <= x + w and y <= b.y <= y + h for x, y, w, h in self.bushes)

    # ------------------------------------------------------------------ step
    def step(self, dt: float, move=(0.0, 0.0), clicks=(), keys=()):
        self.t += dt
        if "r" in keys and self.game_over:
            self.reset()
            return
        if self.game_over:
            return
        self.alive_time += dt
        p = self.player
        mx, my = move or (0.0, 0.0)
        n = math.hypot(mx, my)
        if n > 1:
            mx, my = mx / n, my / n
        self._move(p, mx * self.PLAYER_SPEED * dt, my * self.PLAYER_SPEED * dt)

        self.fire_timer -= dt
        for cx, cy in clicks:
            if self.fire_timer <= 0:
                dx, dy = cx - p.x, cy - p.y
                d = math.hypot(dx, dy) or 1
                self.bullets.append(Body(p.x, p.y, 4, dx / d * self.BULLET_SPEED, dy / d * self.BULLET_SPEED))
                self.fire_timer = self.FIRE_COOLDOWN

        self.spawn_timer -= dt
        if self.spawn_timer <= 0:
            self.spawn_timer = self.SPAWN_EVERY
            if len(self.enemies) < self.MAX_ENEMIES:
                self.enemies.append(self._edge_spawn())
            if len(self.coins) < 3:
                cx, cy = self.rng.uniform(30, W - 30), self.rng.uniform(50, H - 30)
                if not self.blocked(cx, cy, 10):
                    self.coins.append(Body(cx, cy, 7))

        hidden = self.in_bush(p)
        for e in self.enemies:
            dx, dy = p.x - e.x, p.y - e.y
            d = math.hypot(dx, dy) or 1
            if not hidden or d < 55:  # chase
                e.vx, e.vy = dx / d * self.ENEMY_SPEED, dy / d * self.ENEMY_SPEED
            elif self.rng.random() < 0.02:  # lost you: wander
                a = self.rng.uniform(0, 2 * math.pi)
                e.vx, e.vy = math.cos(a) * self.ENEMY_SPEED * 0.6, math.sin(a) * self.ENEMY_SPEED * 0.6
            self._move(e, e.vx * dt, e.vy * dt)

        for b in self.bullets:
            b.x += b.vx * dt
            b.y += b.vy * dt
        self.bullets = [b for b in self.bullets
                        if 0 <= b.x <= W and 0 <= b.y <= H and not self.blocked(b.x, b.y, 0)]
        for b in list(self.bullets):
            for e in list(self.enemies):
                if _touch(b, e):
                    self.enemies.remove(e)
                    self.bullets.remove(b)
                    self.score += 3
                    self.kills += 1
                    break

        for c in list(self.coins):
            if _touch(c, p):
                self.coins.remove(c)
                self.score += 1
                self.coins_taken += 1

        self.hurt_timer -= dt
        for e in list(self.enemies):
            if _touch(e, p) and self.hurt_timer <= 0:
                self.hp -= 1
                self.hurt_timer = 1.0
                self.enemies.remove(e)
        if self.hp <= 0:
            self.game_over = True

    def _edge_spawn(self) -> Body:
        side = self.rng.randrange(4)
        x = self.rng.uniform(20, W - 20)
        y = self.rng.uniform(40, H - 20)
        if side == 0:
            x = 15
        elif side == 1:
            x = W - 15
        elif side == 2:
            y = 45
        else:
            y = H - 15
        return Body(x, y, 12)

    # ------------------------------------------------------------------ render
    def render(self, surface):
        import pygame

        surface.fill(BG)
        for x, y, w, h in self.bushes:
            pygame.draw.rect(surface, BUSH, (x, y, w, h), border_radius=10)
        for wall in self.walls:
            pygame.draw.rect(surface, WALL, wall)
        for c in self.coins:
            pygame.draw.circle(surface, COIN, (int(c.x), int(c.y)), int(c.r))
        for e in self.enemies:
            if not self.in_bush(e):
                pygame.draw.circle(surface, ENEMY, (int(e.x), int(e.y)), int(e.r))
        for b in self.bullets:
            pygame.draw.circle(surface, WHITE, (int(b.x), int(b.y)), int(b.r))
        p = self.player
        pygame.draw.circle(surface, PLAYER, (int(p.x), int(p.y)), int(p.r))
        font = _font()
        surface.blit(font.render(f"SCORE {self.score}   HP {self.hp}", True, WHITE), (10, 8))
        if self.game_over:
            pygame.draw.rect(surface, GAMEOVER, (W // 2 - 170, H // 2 - 40, 340, 80), border_radius=8)
            txt = font.render("GAME OVER - press R", True, WHITE)
            surface.blit(txt, txt.get_rect(center=(W // 2, H // 2)))


_FONT = None


def _font():
    global _FONT
    import pygame

    if _FONT is None:
        pygame.font.init()
        _FONT = pygame.font.SysFont("consolas,menlo,monospace", 20, bold=True)
    return _FONT


def _touch(a: Body, b: Body) -> bool:
    return math.hypot(a.x - b.x, a.y - b.y) < a.r + b.r


def _overlap(a, b, pad=0):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw + pad and bx < ax + aw + pad and ay < by + bh + pad and by < ay + ah + pad


def main():
    import argparse

    import pygame

    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    os.environ.setdefault("SDL_VIDEO_CENTERED", "1")
    pygame.init()
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("sightflow arena")
    clock = pygame.time.Clock()
    game = Arena(a.seed)
    running = True
    while running:
        clicks, keys = [], []
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                clicks.append(ev.pos)
            elif ev.type == pygame.KEYDOWN and ev.key == pygame.K_r:
                keys.append("r")
        k = pygame.key.get_pressed()
        move = (k[pygame.K_d] - k[pygame.K_a], k[pygame.K_s] - k[pygame.K_w])
        game.step(clock.tick(60) / 1000, move, clicks, keys)
        game.render(screen)
        pygame.display.flip()
    pygame.quit()


if __name__ == "__main__":
    main()
