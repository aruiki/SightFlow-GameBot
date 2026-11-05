"""Tactical arbitration for screen-driven macros (hide / fight / rotate).

Why this is not hard to parallelise
-----------------------------------
Every tick reads ONE snapshot of the world and evaluates every behaviour.
Behaviours write to separate channels:

    movement channel : one vector (dx, dy) -> WASD keys
    attack channel   : one boolean            -> attack key / click

Only movement can conflict, and it is resolved by ONE steering score that
already contains every requirement at the same time:

    target attraction + gas clearance + enemy avoidance + screen edges + stuck escape

So "walk to a bush while staying out of the poison gas" is a single decision,
not two behaviours fighting each other. There are no threads and no async:
the result is deterministic per tick, easy to test and replay from a video.

Coordinate convention: screen pixels, x right, y down (same as Detection).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

INF = math.inf

Box = Any  # Detection-like: x, y (centre), w, h, label, id, score, exists


# --------------------------------------------------------------------- config
@dataclass(frozen=True)
class PolicyConfig:
    """Tunables. Defaults are for a 960x540 capture at 20 fps."""

    # combat
    enemy_range: float = 320.0       # attack when the enemy is closer than this
    enemy_avoid: float = 140.0       # start penalising bushes / directions near enemies
    attack_cooldown: float = 0.35    # seconds between attack presses

    # poison gas
    gas_margin: float = 80.0         # preferred clearance from gas
    gas_danger: float = 160.0        # below this, rotating to safety wins over hiding

    # steering
    lookahead: float = 90.0          # how far ahead each sampled direction is checked
    directions: int = 16             # sampled directions (8 = 45 deg, 16 = 22.5 deg)
    edge_margin: float = 40.0

    # target selection / anti-oscillation
    switch_margin: float = 0.18      # a new bush must be this much better to steal the target
    min_dwell: float = 0.4           # and the current target is kept at least this long
    target_radius: float = 45.0      # close enough to count as "inside this bush"

    # stuck detection
    stuck_time: float = 0.6          # blocked for this long -> sidestep
    stuck_speed: float = 25.0        # px/s
    stuck_hold: float = 0.6          # keep sidestepping this long

    # weights
    w_target: float = 1.0
    w_safe: float = 2.0
    w_escape: float = 2.5
    w_enemy: float = 1.8
    w_edge: float = 1.0
    w_stuck: float = 2.5
    w_hide: float = 0.6
    w_travel: float = 0.5


# --------------------------------------------------------------------- result
@dataclass
class Decision:
    move: tuple[float, float] | None
    attack: bool
    attack_target: Box | None
    mode: str                       # hide / approach / rotate / explore / stuck
    reason: str
    target: Box | None = None
    target_score: float = 0.0
    clearance: float = INF
    scores: list[tuple[Box, float]] = field(default_factory=list)


# ------------------------------------------------------------------ geometry
def _exists(b: Box) -> bool:
    return bool(getattr(b, "exists", True))


def _area(b: Box) -> float:
    poly = getattr(b, "polygon", None)
    if poly and len(poly) >= 3:
        area = 0.0
        for i in range(len(poly)):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % len(poly)]
            area += x1 * y2 - x2 * y1
        return abs(area) / 2
    return max(0.0, float(getattr(b, "w", 0.0))) * max(0.0, float(getattr(b, "h", 0.0)))


def _point_in_polygon(poly, x: float, y: float) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-9) + x1
            if x < xin:
                inside = not inside
    return inside


def _inside(b: Box, x: float, y: float, pad: float = 0.0) -> bool:
    poly = getattr(b, "polygon", None)
    if poly and len(poly) >= 3:
        return _point_in_polygon(poly, x, y)
    return (abs(x - float(b.x)) <= float(getattr(b, "w", 0.0)) / 2 + pad
            and abs(y - float(b.y)) <= float(getattr(b, "h", 0.0)) / 2 + pad)


def _box_distance(b: Box, x: float, y: float) -> float:
    """Shortest distance from (x, y) to the box; 0 when inside."""
    left = float(b.x) - float(getattr(b, "w", 0.0)) / 2
    right = float(b.x) + float(getattr(b, "w", 0.0)) / 2
    top = float(b.y) - float(getattr(b, "h", 0.0)) / 2
    bottom = float(b.y) + float(getattr(b, "h", 0.0)) / 2
    dx = max(left - x, 0.0, x - right)
    dy = max(top - y, 0.0, y - bottom)
    return math.hypot(dx, dy)


def _center(frame_w: float, frame_h: float) -> Box:
    class _Me:
        label, id, score = "me", -1, 1.0
        exists = True
        w, h = 2.0, 2.0
        x, y = frame_w / 2, frame_h / 2
    return _Me()


class GasField:
    """Poison gas as boxes and/or a boolean mask (True = gas).

    Boxes are enough for the first version (colour/blob detections). A mask
    gives a proper clearance field via a distance transform when OpenCV is
    available, and is the better representation for a real shrinking ring.
    """

    def __init__(self, boxes: Sequence[Box] = (), mask=None, frame_size=None):
        boxes = [b for b in boxes if _exists(b)]
        polys = [b for b in boxes if getattr(b, "polygon", None) and len(b.polygon) >= 3]
        if polys:
            try:
                import cv2
                import numpy as np

                if mask is not None:
                    h, w = mask.shape[:2]
                elif frame_size:
                    h, w = int(frame_size[1]), int(frame_size[0])
                else:
                    h = int(max(max(p[1] for p in b.polygon) for b in polys)) + 2
                    w = int(max(max(p[0] for p in b.polygon) for b in polys)) + 2
                base = np.asarray(mask, bool).copy() if mask is not None else np.zeros((h, w), bool)
                for b in polys:
                    pts = np.array(b.polygon, np.int32)
                    cv2.fillPoly(base.view(np.uint8), [pts], 1)
                mask = base
            except Exception:      # pragma: no cover - optional acceleration
                pass
        self.boxes = boxes
        self.mask = mask
        self._dist = None
        if mask is not None:
            try:
                import cv2
                import numpy as np

                m = np.asarray(mask)
                if m.any():
                    self._dist = cv2.distanceTransform((~m).astype(np.uint8), cv2.DIST_L2, 5)
            except Exception:      # pragma: no cover - optional acceleration
                self._dist = None

    @property
    def empty(self) -> bool:
        if self.mask is not None:
            try:
                return not bool(self.mask.any())
            except Exception:
                return False
        return not self.boxes

    def covers(self, x: float, y: float) -> bool:
        if self._dist is not None:
            h, w = self._dist.shape
            return bool(0 <= int(y) < h and 0 <= int(x) < w
                        and self._dist[int(y), int(x)] <= 0)
        return any(_inside(b, x, y) for b in self.boxes)

    def clearance(self, x: float, y: float) -> float:
        """Distance to the nearest gas; INF when there is none."""
        best = INF
        if self._dist is not None:
            h, w = self._dist.shape
            if 0 <= int(y) < h and 0 <= int(x) < w:
                best = float(self._dist[int(y), int(x)])
        for b in self.boxes:
            best = min(best, _box_distance(b, x, y))
        return best

    def escape_dir(self, x: float, y: float) -> tuple[float, float] | None:
        """Unit vector pointing away from the closest gas (None = no gas)."""
        if self.empty:
            return None
        if self._dist is not None:
            h, w = self._dist.shape
            ix, iy = int(x), int(y)
            if not (0 <= ix < w and 0 <= iy < h):
                return None
            x0, x1 = max(0, ix - 2), min(w - 1, ix + 2)
            y0, y1 = max(0, iy - 2), min(h - 1, iy + 2)
            gx = float(self._dist[iy, x1]) - float(self._dist[iy, x0])
            gy = float(self._dist[y1, ix]) - float(self._dist[y0, ix])
            n = math.hypot(gx, gy)
            return (gx / n, gy / n) if n > 1e-6 else None
        # boxes: push away from the nearest point of the nearest box
        best_d, best_v = INF, None
        for b in self.boxes:
            left = b.x - b.w / 2
            right = b.x + b.w / 2
            top = b.y - b.h / 2
            bottom = b.y + b.h / 2
            nx, ny = min(max(x, left), right), min(max(y, top), bottom)
            vx, vy = x - nx, y - ny
            d = math.hypot(vx, vy)
            if d < 1e-6:                      # inside: leave via the nearest side
                sides = [(x - left, (-1.0, 0.0)), (right - x, (1.0, 0.0)),
                         (y - top, (0.0, -1.0)), (bottom - y, (0.0, 1.0))]
                d, v = min(sides, key=lambda s: s[0])
                best_d, best_v = d, v
                continue
            if d < best_d:
                best_d, best_v = d, (vx / d, vy / d)
        return best_v


EMPTY_GAS = GasField()


# ------------------------------------------------------------------ scoring
def score_bushes(bushes: Iterable[Box], me: Box, enemies: Sequence[Box],
                 gas: GasField, frame_w: float, frame_h: float,
                 cfg: PolicyConfig) -> list[tuple[Box, float]]:
    """Rank bushes by safety, hiding quality, travel cost and enemy threat."""
    diag = math.hypot(frame_w, frame_h) or 1.0
    out: list[tuple[Box, float]] = []
    for b in bushes:
        if not _exists(b):
            continue
        clear = gas.clearance(b.x, b.y)
        if gas.empty:
            safe = 1.0
        else:
            safe = min(clear / (2 * cfg.gas_margin), 1.0)
            if clear < cfg.gas_margin:
                safe *= 0.25
        hide = min(_area(b) / (frame_w * frame_h * 0.02), 1.0)
        travel = 1.0 - min(math.hypot(b.x - me.x, b.y - me.y) / diag, 1.0)
        threat = 0.0
        for e in enemies:
            d = math.hypot(b.x - e.x, b.y - e.y)
            if d < cfg.enemy_avoid:
                threat += 1.0 - d / cfg.enemy_avoid
        score = (cfg.w_safe * safe + cfg.w_hide * hide
                 + cfg.w_travel * travel - cfg.w_enemy * threat)
        out.append((b, score))
    return out


# ----------------------------------------------------------------- steering
def steer(me: Box, target: Box | None, gas: GasField, enemies: Sequence[Box],
          frame_w: float, frame_h: float, cfg: PolicyConfig,
          stuck_dir: tuple[float, float] | None = None
          ) -> tuple[tuple[float, float], float, float]:
    """Pick one movement direction by sampling.

    Returns (unit vector, score, clearance at the chosen look-ahead).
    Every requirement contributes to the same score, so there is no
    "behaviour A vs behaviour B" conflict at runtime.
    """
    if target is not None:
        tx, ty = float(target.x) - me.x, float(target.y) - me.y
    else:
        esc = gas.escape_dir(me.x, me.y) if not gas.empty else None
        tx, ty = (esc[0] * 200, esc[1] * 200) if esc else (0.0, -200.0)

    tn = math.hypot(tx, ty)
    tdir = (tx / tn, ty / tn) if tn > 1e-6 else (0.0, -1.0)

    clear_me = gas.clearance(me.x, me.y)
    danger = (not gas.empty) and clear_me < cfg.gas_danger
    esc_dir = gas.escape_dir(me.x, me.y) if (danger and not gas.empty) else None

    best: tuple[float, float, float, float] | None = None
    for i in range(cfg.directions):
        a = 2 * math.pi * i / cfg.directions
        dx, dy = math.cos(a), math.sin(a)

        raw_x, raw_y = me.x + dx * cfg.lookahead, me.y + dy * cfg.lookahead
        clear = gas.clearance(min(max(raw_x, 0.0), frame_w),
                              min(max(raw_y, 0.0), frame_h))

        if gas.empty:
            safe = 1.0
        else:
            safe = min(clear / (2 * cfg.gas_margin), 1.0)
            if clear < cfg.gas_margin:
                safe *= 0.25
            # Moving to a place with more clearance is good even when we are
            # already standing in gas (clear_me == 0 for every sample).
            if clear_me < INF:
                safe += max(-1.0, min(1.0, (clear - clear_me) / (2 * cfg.gas_margin)))

        target_term = dx * tdir[0] + dy * tdir[1]

        threat = 0.0
        for e in enemies:
            ex, ey = float(e.x) - me.x, float(e.y) - me.y
            ed = math.hypot(ex, ey)
            if 1e-6 < ed < cfg.enemy_avoid:
                toward = (dx * ex + dy * ey) / ed
                threat += (1.0 - ed / cfg.enemy_avoid) * (0.5 + 0.5 * max(0.0, toward))

        edge = 0.0
        m = cfg.edge_margin
        if raw_x < m or raw_x > frame_w - m or raw_y < m or raw_y > frame_h - m:
            edge = 1.0

        stuck_score = 0.0
        if stuck_dir is not None:
            stuck_score = dx * stuck_dir[0] + dy * stuck_dir[1]

        escape = 0.0
        # When we are already inside gas and have a target, let the target and
        # clearance terms decide; a box's "nearest side" is arbitrary there.
        if esc_dir is not None and (target is None or clear_me > 1.0):
            escape = dx * esc_dir[0] + dy * esc_dir[1]

        score = (cfg.w_target * target_term + cfg.w_safe * safe
                 + cfg.w_escape * escape - cfg.w_enemy * threat
                 - cfg.w_edge * edge + cfg.w_stuck * stuck_score)

        if best is None or score > best[0] + 1e-12:
            best = (score, dx, dy, clear)

    assert best is not None
    return (best[1], best[2]), best[0], best[3]


# -------------------------------------------------------------------- policy
class TacticalPolicy:
    """Stateful per-tick policy. One instance per match.

    Channels:
      - movement: exactly one vector, or None to stand still
      - attack:   independent boolean, so you can shoot while moving/hiding
    """

    def __init__(self, cfg: PolicyConfig | None = None):
        self.cfg = cfg or PolicyConfig()
        self.target_id: int | None = None
        self.target_since: float = 0.0
        self.last_attack: float = -INF
        self._last_pos: tuple[float, float] | None = None
        self._last_time: float | None = None
        self._last_move: tuple[float, float] | None = None
        self._still: float = 0.0
        self._stuck_until: float = -INF
        self._stuck_side: int = 1
        self._stuck_dir: tuple[float, float] | None = None

    def reset(self):
        self.__init__(self.cfg)

    def tick(self, now: float, me: Box | None, bushes: Sequence[Box],
             enemies: Sequence[Box] = (), gas: GasField | None = None,
             frame_w: float = 960, frame_h: float = 540) -> Decision:
        cfg = self.cfg
        gas = gas or EMPTY_GAS
        if me is None or not _exists(me):
            me = _center(frame_w, frame_h)
        bushes = [b for b in bushes if _exists(b)]
        enemies = [e for e in enemies if _exists(e)]

        self._update_stuck(now, me)

        scored = score_bushes(bushes, me, enemies, gas, frame_w, frame_h, cfg)
        target, target_score = self._pick(now, scored)
        clear = gas.clearance(me.x, me.y)
        danger = (not gas.empty) and clear < cfg.gas_danger
        in_target = target is not None and (
            _inside(target, me.x, me.y)
            or math.hypot(target.x - me.x, target.y - me.y) <= cfg.target_radius)
        stuck = now < self._stuck_until

        if danger:
            # Highest priority: leave the gas. Prefer a bush that is still safe.
            safe_target = target if (target is not None and
                                     gas.clearance(target.x, target.y) >= cfg.gas_margin) else None
            move, _, _ = steer(me, safe_target, gas, enemies, frame_w, frame_h, cfg,
                               stuck_dir=self._stuck_dir if stuck else None)
            mode, reason = "rotate", f"gas {clear:.0f}px"
        elif in_target:
            move, mode, reason = None, "hide", "in bush"
        elif target is not None:
            move, _, _ = steer(me, target, gas, enemies, frame_w, frame_h, cfg,
                               stuck_dir=self._stuck_dir if stuck else None)
            mode, reason = "approach", f"bush #{getattr(target, 'id', '?')}"
        else:
            move, _, _ = steer(me, None, gas, enemies, frame_w, frame_h, cfg,
                               stuck_dir=self._stuck_dir if stuck else None)
            mode = "rotate" if danger else "explore"
            reason = "no bush visible"

        if stuck and mode != "rotate":
            mode = "stuck"
            reason = "blocked -> sidestep"

        attack_target, attack = self._attack(now, me, enemies)
        if attack:
            reason += " + attack"

        self._last_pos = (me.x, me.y)
        self._last_time = now
        self._last_move = move
        return Decision(move=move, attack=attack, attack_target=attack_target,
                        mode=mode, reason=reason, target=target,
                        target_score=target_score, clearance=clear,
                        scores=[(b, s) for b, s in scored])

    # --------------------------------------------------------------- helpers
    def _pick(self, now: float, scored: list[tuple[Box, float]]) -> tuple[Box | None, float]:
        if not scored:
            self.target_id = None
            return None, 0.0
        best_b, best_s = max(scored, key=lambda x: x[1])
        chosen_b, chosen_s = best_b, best_s
        if self.target_id is not None:
            cur = next((x for x in scored
                        if getattr(x[0], "id", None) == self.target_id), None)
            if cur is not None and best_b is not cur[0]:
                # Hysteresis: keep the current target unless the new one is
                # clearly better and the old one has been used long enough.
                if (best_s - cur[1] < self.cfg.switch_margin
                        or now - self.target_since < self.cfg.min_dwell):
                    chosen_b, chosen_s = cur[0], cur[1]
        new_id = getattr(chosen_b, "id", None)
        if new_id != self.target_id:
            self.target_id, self.target_since = new_id, now
        return chosen_b, chosen_s

    def _attack(self, now: float, me: Box,
                enemies: Sequence[Box]) -> tuple[Box | None, bool]:
        if not enemies:
            return None, False
        target = min(enemies, key=lambda e: math.hypot(e.x - me.x, e.y - me.y))
        d = math.hypot(target.x - me.x, target.y - me.y)
        if d <= self.cfg.enemy_range and now - self.last_attack >= self.cfg.attack_cooldown:
            self.last_attack = now
            return target, True
        return target, False

    def _update_stuck(self, now: float, me: Box):
        if self._last_time is not None:
            dt = now - self._last_time
            if dt > 0:
                moved = math.hypot(me.x - self._last_pos[0], me.y - self._last_pos[1])
                requested = self._last_move is not None
                if requested and moved < max(1.0, self.cfg.stuck_speed * dt * 0.5):
                    self._still += dt
                else:
                    self._still = 0.0
                if self._still >= self.cfg.stuck_time:
                    self._still = 0.0
                    self._stuck_until = now + self.cfg.stuck_hold
                    self._stuck_side *= -1
                    lm = self._last_move or (0.0, -1.0)
                    self._stuck_dir = (-lm[1] * self._stuck_side,
                                       lm[0] * self._stuck_side)
