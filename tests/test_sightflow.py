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


def test_template_detector_japanese_path(tmp_path):
    # Windows: cv2.imread/imwrite fail on paths like ...\ドキュメント\...
    from sightflow.imageio import imread, imwrite

    img = _scene()
    folder = tmp_path / "ドキュメント"
    folder.mkdir()
    assert imwrite(folder / "敵.png", img[86:115, 136:165])
    assert imread(folder / "敵.png").shape == (29, 29, 3)
    assert imread(folder / "ない.png") is None
    spec = {"type": "template", "label": "enemy", "image": "敵.png", "threshold": 0.9}
    dets = build_detectors([spec], folder)[0].detect(img)
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


# ------------------------------------------------------------------ new actions
class _Frame:
    w, h = 400, 200


def _tick_one(action, me=(100, 100), enemy=(200, 100)):
    from sightflow.detection import LabelGroup
    f = Flow({"states": {"s": {"rules": [{"do": [action]}]}}})
    e = Detection("enemy", *enemy, 10, 10)
    m = Detection("player", *me, 10, 10)
    return f.tick({"enemy": LabelGroup("enemy", [e])}, m, 0.0, extra={"frame": _Frame()}).intents


def test_aim_drags_from_button_toward_target():
    it = _tick_one({"aim": {"button": [0.9, 0.8], "target": "enemy.nearest", "length": 50}})
    fx, fy, tx, ty, secs = it.drags[0]
    assert (fx, fy) == pytest.approx((360, 160))          # fractions of the frame
    assert (tx, ty) == pytest.approx((410, 160))          # 50px to the right (enemy is right of me)
    assert secs == pytest.approx(0.15)


def test_drag_hold_move():
    it = _tick_one({"drag": {"from": [10, 10], "to": "enemy.nearest", "seconds": 0.2}})
    assert it.drags == [(10, 10, 200, 100, 0.2)]
    assert _tick_one({"hold": {"key": "e", "seconds": 0.5}}).holds == [("e", 0.5)]
    assert _tick_one({"move": [0, -3]}).move == pytest.approx((0, -1))
    with pytest.raises(ValueError):
        _tick_one({"aim": {"button": [1, 1]}})              # target missing


def test_detector_offset_and_aspect():
    img = np.full((100, 200, 3), 30, np.uint8)
    cv2.rectangle(img, (20, 20), (79, 27), (40, 200, 40), -1)   # wide green bar 60x8
    cv2.rectangle(img, (120, 20), (139, 39), (40, 200, 40), -1)  # green square 20x20
    spec = {"type": "color", "label": "bar", "hsv": [[50, 150, 150], [70, 255, 255]],
            "aspect": [3, 15], "offset": [0, 30]}
    dets = build_detectors([spec], ROOT)[0].detect(img)
    assert len(dets) == 1
    assert (dets[0].x, dets[0].y) == pytest.approx((50, 54), abs=1)


# ------------------------------------------------------------------ motion / stuck
def _texture(seed=0, size=(240, 320)):
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, (size[0] // 8, size[1] // 8, 3), np.uint8)
    return cv2.resize(small, (size[1], size[0]), interpolation=cv2.INTER_NEAREST)


def test_stuck_when_trying_to_move_but_not_moving():
    from sightflow.motion import MotionTracker
    m = MotionTracker(min_speed=25)
    bg = _texture()
    me = Detection("player", 160, 120, 10, 10)
    for i in range(10):
        m.update(bg, me, i * 0.05)
        m.after_decision((1.0, 0.0))                      # keep pushing right
    assert m.stuck_time == pytest.approx(0.45, abs=0.06)
    m.after_decision(None)                                # standing still is not stuck
    m.update(bg, me, 0.55)
    assert m.stuck_time == 0


def test_not_stuck_when_me_moves_on_fixed_camera():
    from sightflow.motion import MotionTracker
    m = MotionTracker(min_speed=25)
    bg = _texture()
    for i in range(10):
        m.update(bg, Detection("player", 50 + i * 5, 120, 10, 10), i * 0.05)   # 100 px/s
        m.after_decision((1.0, 0.0))
    assert m.stuck_time == 0 and m.speed > 60


def test_not_stuck_when_camera_follows_me():
    from sightflow.motion import MotionTracker
    m = MotionTracker(min_speed=25)
    big = _texture(1, (240, 640))
    me = Detection("player", 160, 120, 10, 10)              # always centred on screen
    for i in range(10):
        frame = np.ascontiguousarray(big[:, i * 8: i * 8 + 320])  # world scrolls left 8px/tick
        m.update(frame, me, i * 0.05)
        m.after_decision((1.0, 0.0))
    assert m.stuck_time == 0
    assert m.speed == pytest.approx(160, rel=0.3)           # 8px / 0.05s


def test_example_configs_parse():
    for path in sorted((ROOT / "examples").glob("*.yaml")):
        cfg, base = load_config(path)
        Flow(cfg["flow"])
        dets = build_detectors(cfg["detectors"], base)
        for d in dets:
            d.detect(np.zeros((360, 640, 3), np.uint8))
