"""One-off integration check: TacticalPolicy driving the Bush Arena demo.

Uses only rendered pixels + the colour detectors from arena_project.json.
Run:  python demo/policy_demo.py            (optionally SEED=1 SECONDS=30)
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from sightflow.behavior import TacticalPolicy
from sightflow.bot import build_groups
from sightflow.detectors import build_detectors
from sightflow.io import build_source
from sightflow.server import detector_specs
from sightflow.tracking import CentroidTracker

proj = json.loads((ROOT / "demo" / "arena_project.json").read_text(encoding="utf-8"))
settings = proj["settings"]

src = build_source({"type": "python", "factory": "bridge:make_sim",
                    "seed": int(os.environ.get("SEED", "0")), "dt": 0.05},
                   ROOT / "demo")
inp = src.input_backend()
detectors = build_detectors(detector_specs(settings["detector"]), ROOT)
labels = [d.label for d in detectors]
tracker = CentroidTracker()
policy = TacticalPolicy()

ticks = hidden = attacks = 0
modes = {}
first_bush_at = None
game = src.game
limit = float(os.environ.get("SECONDS", "60"))
while not game.game_over and game.t < limit:
    frame = src.grab()
    dets = tracker.update([d for det in detectors for d in det.detect(frame)])
    groups, me = build_groups(dets, labels, settings.get("anchor"), frame.shape)
    bushes = groups["bush"].items if "bush" in groups else []
    enemies = groups["enemy"].items if "enemy" in groups else []
    dec = policy.tick(src.time(), me, bushes, enemies,
                      frame_w=frame.shape[1], frame_h=frame.shape[0])
    inp.set_move(dec.move)
    if dec.attack and dec.attack_target is not None:
        attacks += 1
        inp.click(dec.attack_target.x, dec.attack_target.y)
    if game.in_bush(game.player):
        hidden += 1
        if first_bush_at is None:
            first_bush_at = game.t
    modes[dec.mode] = modes.get(dec.mode, 0) + 1
    ticks += 1

print(f"sim time      : {game.t:.1f}s / ticks {ticks}")
print(f"in bush       : {hidden}/{ticks} = {100 * hidden / max(ticks, 1):.0f}%")
print(f"first bush at : {first_bush_at if first_bush_at is not None else '-'} s")
print(f"modes         : {modes}")
print(f"attacks       : {attacks}")
print(f"score/kills/hp: {game.score} / {game.kills} / {game.hp}")
print(f"game over     : {game.game_over}")
