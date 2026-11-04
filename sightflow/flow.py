"""Rule-flow engine: states, ordered rules, actions, cooldowns.

Each tick, inside the current state, rules are checked top to bottom and the
FIRST rule whose `when` is true fires (a rule without `when` always matches).
`global` rules are checked before the state's rules, in every state.

    flow:
      initial: explore
      global:
        - when: gameover.any
          do: [{key: r}]
          cooldown: 1.0
      states:
        explore:
          rules:
            - when: enemy.nearest.distance < 200
              goto: fight
            - do: [{move_toward: coin.nearest}]
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .detection import Detection, LabelGroup
from .expr import Expr


class Vars:
    """User memory for `set` actions. Unknown names read as None."""

    def __init__(self):
        object.__setattr__(self, "_d", {})

    def __getattr__(self, name):
        return self._d.get(name)

    def __setattr__(self, name, value):
        self._d[name] = value

    def as_dict(self):
        return dict(self._d)


# ---------------------------------------------------------------- intents
@dataclass
class Intents:
    """What the flow wants the input backend to do this tick."""

    move: tuple[float, float] | None = None  # unit-ish vector, None = stand still
    taps: list[str] = field(default_factory=list)
    clicks: list[tuple[float, float]] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- actions
def _target_xy(value: Any, names: dict) -> tuple[float, float] | None:
    if isinstance(value, Expr):
        value = value(names)
    if isinstance(value, Detection):
        return (value.x, value.y) if value.exists else None
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return float(value[0]), float(value[1])
    return None


class Action:
    KINDS = ("key", "move_toward", "move_away", "click", "set", "log")

    def __init__(self, spec: dict):
        spec = dict(spec)
        self.cooldown = float(spec.pop("cooldown", 0.0))
        kinds = [k for k in spec if k in self.KINDS]
        if len(kinds) != 1:
            raise ValueError(f"action needs exactly one of {self.KINDS}, got {spec}")
        self.kind = kinds[0]
        raw = spec.pop(self.kind)
        if self.kind in ("move_toward", "move_away", "click") and isinstance(raw, str):
            raw = Expr(raw)
        if self.kind == "set":
            raw = {k: Expr(v) if isinstance(v, str) else v for k, v in raw.items()}
        self.arg = raw
        self.last_fired = -math.inf

    def ready(self, now: float) -> bool:
        return now - self.last_fired >= self.cooldown

    def run(self, names: dict, out: Intents, vars_: Vars, now: float) -> bool:
        if not self.ready(now):
            return False
        k = self.kind
        if k == "key":
            out.taps.append(str(self.arg))
        elif k in ("move_toward", "move_away"):
            xy = _target_xy(self.arg, names)
            if xy is None:
                return False
            me = names["me"]
            vx, vy = xy[0] - me.x, xy[1] - me.y
            n = math.hypot(vx, vy)
            if n < 1e-6:
                return False
            sign = 1 if k == "move_toward" else -1
            out.move = (sign * vx / n, sign * vy / n)
        elif k == "click":
            xy = _target_xy(self.arg, names)
            if xy is None:
                return False
            out.clicks.append(xy)
        elif k == "set":
            for name, v in self.arg.items():
                setattr(vars_, name, v(names) if isinstance(v, Expr) else v)
        elif k == "log":
            out.logs.append(str(self.arg))
        self.last_fired = now
        return True


# ---------------------------------------------------------------- rules / states
class Rule:
    def __init__(self, spec: dict, index: int):
        self.name = spec.get("name") or f"#{index}"
        self.when = Expr(spec["when"]) if "when" in spec else None
        self.actions = [Action(a) for a in spec.get("do", [])]
        self.goto = spec.get("goto")
        self.cooldown = float(spec.get("cooldown", 0.0))
        self.last_fired = -math.inf
        if self.when is not None:
            self.name = spec.get("name") or self.when.source

    def matches(self, names: dict, now: float) -> bool:
        if now - self.last_fired < self.cooldown:
            return False
        return self.when is None or bool(self.when(names))


class State:
    def __init__(self, name: str, spec: dict):
        self.name = name
        self.rules = [Rule(r, i) for i, r in enumerate(spec.get("rules", []))]
        self.on_enter = [Action(a) for a in spec.get("on_enter", [])]


@dataclass
class TickResult:
    state: str
    rule: str | None
    intents: Intents


class Flow:
    def __init__(self, spec: dict):
        self.states = {n: State(n, s or {}) for n, s in spec["states"].items()}
        self.global_rules = [Rule(r, i) for i, r in enumerate(spec.get("global", []))]
        self.initial = spec.get("initial") or next(iter(self.states))
        for r in self.global_rules + [r for s in self.states.values() for r in s.rules]:
            if r.goto and r.goto not in self.states:
                raise ValueError(f"rule {r.name!r} goes to unknown state {r.goto!r}")
        if self.initial not in self.states:
            raise ValueError(f"initial state {self.initial!r} not defined")
        self.vars = Vars()
        self.current = self.initial
        self.entered_at: float | None = None

    def reset(self):
        self.current = self.initial
        self.entered_at = None
        self.vars = Vars()

    def tick(self, groups: dict[str, LabelGroup], me: Detection, now: float,
             extra: dict | None = None) -> TickResult:
        out = Intents()
        if self.entered_at is None:
            self._enter(self.current, groups, me, now, out)
        names = self._names(groups, me, now, extra)
        fired = None
        for rule in self.global_rules + self.states[self.current].rules:
            if rule.matches(names, now):
                fired = rule
                break
        if fired:
            fired.last_fired = now
            for a in fired.actions:
                a.run(names, out, self.vars, now)
            if fired.goto and fired.goto != self.current:
                self._enter(fired.goto, groups, me, now, out)
        return TickResult(self.current, fired.name if fired else None, out)

    def _enter(self, state, groups, me, now, out):
        self.current = state
        self.entered_at = now
        names = self._names(groups, me, now, None)
        for a in self.states[state].on_enter:
            a.run(names, out, self.vars, now)

    def _names(self, groups, me, now, extra):
        names: dict[str, Any] = dict(groups)
        names.update(
            me=me,
            vars=self.vars,
            state=self.current,
            state_time=now - (self.entered_at if self.entered_at is not None else now),
            t=now,
        )
        if extra:
            names.update(extra)
        return names
