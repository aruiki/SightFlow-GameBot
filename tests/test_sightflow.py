import math
import os
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "demo")]
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from sightflow import Bot, load_config  # noqa: E402
from sightflow.bot import build_groups  # noqa: E402
from sightflow.detection import Detection  # noqa: E402
from sightflow.detectors import build_detectors  # noqa: E402
from sightflow.expr import Expr, ExprError  # noqa: E402
from sightflow.flow import Flow  # noqa: E402


# ------------------------------------------------------------------ expressions
def test_expr_basic():
    assert Expr("a + 1 > 2 and min(a, 5) == 3")({"a": 3})


@pytest.mark.parametrize("bad", [
    "__import__('os')",
    "a.__class__",
    "open('x')",
    "[x for x in a]",
    "lambda: 1",
    "a.within(r=1)",
])
def test_expr_rejects_unsafe(bad):
    with pytest.raises(ExprError):
        Expr(bad)


# ------------------------------------------------------------------ detectors
def _scene():
    img = np.full((200, 300, 3), (36, 30, 28), np.uint8)
    cv2.circle(img, (50, 100), 12, (255, 120, 40), -1)   # blue player (BGR)
    cv2.circle(img, (150, 100), 12, (40, 40, 230), -1)   # red enemy
    cv2.circle(img, (250, 100), 12, (40, 40, 230), -1)   # red enemy, farther
    return img


SPECS = [
    {"type": "color", "label": "player", "hsv": [[100, 150, 150], [118, 255, 255]], "min_area": 100},
    {"type": "color", "label": "enemy", "hsv": [[0, 150, 120], [8, 255, 255]], "min_area": 100},
    {"type": "color", "label": "coin", "hsv": [[20, 200, 200], [30, 255, 255]]},
]


def test_color_detector_and_groups():
    img = _scene()
    dets = [d for det in build_detectors(SPECS, ROOT) for d in det.detect(img)]
    groups, me = build_groups(dets, ["player", "enemy", "coin"], "player", img.shape)
    assert me.exists and abs(me.x - 50) < 2
    assert groups["enemy"].count == 2
    assert abs(groups["enemy"].nearest.distance - 100) < 2
    assert groups["enemy"].within(150) == 1
    assert not groups["coin"].any                        # declared but absent
    assert math.isinf(groups["coin"].nearest.distance)   # safe to compare


def test_roi_offsets_coordinates():
    spec = dict(SPECS[1], roi=[200, 0, 100, 200])
    dets = build_detectors([spec], ROOT)[0].detect(_scene())
    assert len(dets) == 1 and abs(dets[0].x - 250) < 2


def test_template_detector(tmp_path):
    img = _scene()
    tpl = img[86:115, 136:165]
    cv2.imwrite(str(tmp_path / "enemy.png"), tpl)
    spec = {"type": "template", "label": "enemy", "image": "enemy.png", "threshold": 0.9}
    dets = build_detectors([spec], tmp_path)[0].detect(img)
    assert sorted(round(d.x / 10) for d in dets) == [15, 25]


# ------------------------------------------------------------------ flow
def _groups(enemy_dist=None):
    me = Detection("player", 0, 0, 10, 10)
    items = []
    if enemy_dist is not None:
        items = [Detection("enemy", enemy_dist, 0, 10, 10, dx=enemy_dist, distance=enemy_dist)]
    from sightflow.detection import LabelGroup
    return {"enemy": LabelGroup("enemy", items)}, me


FLOW = {
    "initial": "idle",
    "states": {
        "idle": {"rules": [
            {"when": "enemy.nearest.distance < 100", "goto": "fight"},
        ]},
        "fight": {
            "on_enter": [{"set": {"fights": "(vars.fights or 0) + 1"}}],
            "rules": [
                {"when": "not enemy.any", "goto": "idle"},
                {"do": [{"click": "enemy.nearest", "cooldown": 1.0}, {"move_away": "enemy.nearest"}]},
            ],
        },
    },
}


def test_flow_transitions_cooldowns_and_vars():
    f = Flow(FLOW)
    r = f.tick(*_groups(), now=0.0)
    assert r.state == "idle" and r.rule is None
    r = f.tick(*_groups(50), now=0.1)
    assert r.state == "fight" and f.vars.fights == 1
    r = f.tick(*_groups(50), now=0.2)
    assert r.intents.clicks == [(50, 0)]
    assert r.intents.move == pytest.approx((-1.0, 0.0))
    r = f.tick(*_groups(50), now=0.5)                    # click on cooldown, move still runs
    assert r.intents.clicks == [] and r.intents.move is not None
    r = f.tick(*_groups(50), now=1.3)
    assert r.intents.clicks == [(50, 0)]
    f.tick(*_groups(), now=1.4)
    assert f.current == "idle"


def test_flow_rejects_unknown_state():
    with pytest.raises(ValueError):
        Flow({"states": {"a": {"rules": [{"goto": "nope"}]}}})


# ------------------------------------------------------------------ config + demo
def test_extends_merges(tmp_path):
    (tmp_path / "base.yaml").write_text("a: {x: 1, y: 2}\nb: 1\n")
    (tmp_path / "child.yaml").write_text("extends: base.yaml\na: {y: 3}\n")
    cfg, _ = load_config(tmp_path / "child.yaml")
    assert cfg == {"a": {"x": 1, "y": 3}, "b": 1}


def test_demo_bot_survives_and_scores():
    cfg, base = load_config(ROOT / "demo" / "arena_sim.yaml")
    bot = Bot(cfg, base)
    game = bot.source.game
    while game.t < 30:
        bot.step()
    assert not game.game_over
    assert game.score > 10
