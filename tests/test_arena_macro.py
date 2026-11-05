"""End-to-end: the arena macro project (tactics node) on rendered pixels."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from sightflow.bot import build_groups  # noqa: E402
from sightflow.detectors import build_detectors  # noqa: E402
from sightflow.graph import Ctx, Graph  # noqa: E402
from sightflow.io import build_source  # noqa: E402
from sightflow.motion import MotionTracker  # noqa: E402
from sightflow.screens import ScreenState  # noqa: E402
from sightflow.server import detector_specs  # noqa: E402
from sightflow.tracking import CentroidTracker  # noqa: E402


def load(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def test_demo_macro_hides_and_attacks(tmp_path):
    proj = load("demo/arena_macro.json")
    settings = proj["settings"]
    graph = Graph(proj["graph"])
    src = build_source({"type": "python", "factory": "bridge:make_sim", "seed": 1, "dt": 0.05},
                       ROOT / "demo")
    inp = src.input_backend()
    detectors = build_detectors(detector_specs(settings["detector"]), ROOT)
    labels = [d.label for d in detectors]
    tracker, motion = CentroidTracker(), MotionTracker()
    screens = ScreenState()
    game = src.game

    hidden = ticks = clicks = 0
    while not game.game_over and game.t < 30:
        frame = src.grab()
        dets = tracker.update([d for det in detectors for d in det.detect(frame)])
        groups, me = build_groups(dets, labels, settings.get("anchor"), frame.shape)
        motion.update(frame, me, src.time())
        ctx = Ctx(groups, me, src.time(), frame.shape[1], frame.shape[0],
                  motion.names(), screens=screens)
        status = graph.tick(ctx)
        assert all(v["error"] is None for v in status.values()), status
        motion.after_decision(ctx.intents.move)
        inp.set_move(ctx.intents.move)
        for x, y in ctx.intents.clicks:
            clicks += 1
            inp.click(x, y)
        if game.in_bush(game.player):
            hidden += 1
        ticks += 1

    assert ticks > 100
    assert hidden / ticks > 0.8, f"hidden {hidden}/{ticks}"
    assert clicks > 0, "tactics never attacked"


def test_app_template_is_valid():
    proj = load("templates/brawl_like.json")
    graph = Graph(proj["graph"])
    assert graph.nodes["tactics"]["type"].type == "tactics"
    assert graph.nodes["s_title"]["type"].type == "screen_is"
    det = proj["settings"]["detector"]
    labels = {c["label"] for c in det["color"]} | {c["label"] for c in det.get("region", [])}
    assert {"player", "enemy", "bush", "gas"} <= labels
    assert {c["label"] for c in det["region"]} == {"bush", "gas"}
    assert proj["settings"]["screens"] == []
