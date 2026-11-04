import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "demo")]
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from sightflow.bot import build_groups  # noqa: E402
from sightflow.detection import Detection, LabelGroup  # noqa: E402
from sightflow.detectors import build_detectors  # noqa: E402
from sightflow.graph import NODE_TYPES, Ctx, Graph, GraphError, node_types_json  # noqa: E402
from sightflow.motion import MotionTracker  # noqa: E402
from sightflow.server import detector_specs  # noqa: E402
from sightflow.tracking import CentroidTracker  # noqa: E402


def g(nodes, edges):
    return Graph({"nodes": [{"id": i, "type": t, "data": d} for i, t, d in nodes],
                  "edges": [{"source": s, "sourceHandle": sh, "target": t, "targetHandle": th}
                            for s, sh, t, th in edges]})


def ctx(enemies=(), now=0.0):
    me = Detection("player", 100, 100, 10, 10)
    items = []
    for i, (x, y) in enumerate(enemies):
        d = Detection("enemy", x, y, 10, 10, id=i + 1)
        d.dx, d.dy = x - 100, y - 100
        d.distance = (d.dx ** 2 + d.dy ** 2) ** 0.5
        items.append(d)
    items.sort(key=lambda d: d.distance)
    return Ctx({"enemy": LabelGroup("enemy", items)}, me, now, 400, 300)


CHAIN = (
    [("e", "objects", {"label": "enemy"}), ("n", "nearest", {}), ("f", "field", {"field": "distance"}),
     ("c", "compare", {"op": "<", "b": 150}), ("a", "aim", {"cooldown": 1}),
     ("m", "move_away", {"priority": 1}), ("h", "move_toward", {"priority": 5})],
    [("e", "list", "n", "list"), ("n", "object", "f", "object"), ("f", "value", "c", "a"),
     ("c", "result", "a", "run"), ("n", "object", "a", "target"), ("n", "object", "m", "target"),
     ("c", "result", "h", "run"), ("n", "object", "h", "target")],
)


def test_chain_fires_and_priority_wins():
    gr = g(*CHAIN)
    c = ctx([(200, 100), (300, 100)])
    st = gr.tick(c)
    assert st["e"]["text"] == "2個"
    assert st["c"]["text"] == "true"
    fx, fy, tx, ty, _ = c.intents.drags[0]
    assert tx > fx and ty == pytest.approx(fy)              # dragged rightwards, toward the enemy
    assert c.intents.move == pytest.approx((1, 0))          # move_toward (prio 5) beats move_away
    assert st["h"]["active"] and not st["m"]["active"]


def test_out_of_range_and_cooldown():
    gr = g(*CHAIN)
    c = ctx([(400, 100)])
    gr.tick(c)
    assert not c.intents.drags and c.intents.move == pytest.approx((-1, 0))
    gr2 = g(*CHAIN)
    c1 = ctx([(150, 100)], now=0.0)
    gr2.tick(c1)
    c2 = ctx([(150, 100)], now=0.5)
    gr2.tick(c2)
    assert len(c1.intents.drags) == 1 and len(c2.intents.drags) == 0   # 1 s cooldown


def test_no_enemy_is_safe():
    gr = g(*CHAIN)
    c = ctx([])
    st = gr.tick(c)
    assert c.intents.move is None and not c.intents.drags
    assert all(s["error"] is None for s in st.values())


def test_cycle_rejected():
    with pytest.raises(GraphError):
        g([("a", "not", {}), ("b", "not", {})],
          [("a", "result", "b", "in"), ("b", "result", "a", "in")])


def test_lock_on_target_by_id():
    # remember the nearest enemy's ID once (cooldown 100s = only the first tick),
    # then keep following that ID even when another enemy becomes nearer.
    gr = g([("e", "objects", {"label": "enemy"}), ("n", "nearest", {}), ("once", "cooldown", {"seconds": 100}),
            ("r", "remember", {}), ("b", "by_id", {}), ("go", "move_toward", {})],
           [("e", "list", "n", "list"),
            ("n", "object", "r", "value"), ("e", "any", "once", "in"), ("once", "out", "r", "set"),
            ("e", "list", "b", "list"), ("r", "value", "b", "id"), ("b", "object", "go", "target")])
    c1 = ctx([(200, 100), (100, 300)])           # id1 at 100px (nearest), id2 at 200px
    gr.tick(c1)
    assert c1.intents.move == pytest.approx((1, 0))
    c2 = ctx([(400, 100), (100, 150)], now=1.0)   # id2 is now nearer, but we stay on id1
    gr.tick(c2)
    assert c2.intents.move == pytest.approx((1, 0))


def test_node_types_json_is_serialisable():
    data = node_types_json()
    json.dumps(data)
    assert {t["type"] for t in data["types"]} == set(NODE_TYPES)


def test_centroid_tracker_keeps_ids():
    tr = CentroidTracker()
    a = [Detection("enemy", 10, 10, 5, 5), Detection("enemy", 200, 10, 5, 5)]
    tr.update(a)
    b = [Detection("enemy", 205, 12, 5, 5), Detection("enemy", 14, 11, 5, 5)]
    tr.update(b)
    assert {(round(d.x), d.id) for d in b} == {(205, a[1].id), (14, a[0].id)}


def test_demo_project_runs_headless():
    from sightflow.io import build_source
    proj = json.loads((ROOT / "demo" / "arena_project.json").read_text(encoding="utf-8"))
    s = proj["settings"]
    graph = Graph(proj["graph"])
    src = build_source({"type": "python", "factory": "bridge:make_sim", "seed": 0}, ROOT / "demo")
    dets_cfg = build_detectors(detector_specs(s["detector"]), ROOT)
    labels = [d.label for d in dets_cfg]
    inp, tr, mo = src.input_backend(), CentroidTracker(), MotionTracker()
    game = src.game
    st = {}
    while game.t < 40:
        frame = src.grab()
        dets = tr.update([d for det in dets_cfg for d in det.detect(frame)])
        groups, me = build_groups(dets, labels, s["anchor"], frame.shape)
        mo.update(frame, me, src.time())
        c = Ctx(groups, me, src.time(), frame.shape[1], frame.shape[0], mo.names())
        st = graph.tick(c)
        mo.after_decision(c.intents.move)
        inp.set_move(c.intents.move)
        for k in c.intents.taps:
            inp.tap(k)
        for d in c.intents.drags:
            inp.drag(*d)
    assert all(v["error"] is None for v in st.values())
    assert not game.game_over and game.score > 15
