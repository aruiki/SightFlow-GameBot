"""Screen / wait / hold / tactics nodes in the graph engine."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sightflow.detection import Detection, LabelGroup  # noqa: E402
from sightflow.graph import Ctx, Graph  # noqa: E402
from sightflow.screens import ScreenState  # noqa: E402

W, H = 960.0, 540.0


def g(nodes, edges=()):
    return Graph({"nodes": [{"id": nid, "type": t, "data": d} for nid, t, d in nodes],
                  "edges": [{"source": s, "sourceHandle": sh, "target": t, "targetHandle": th}
                            for s, sh, t, th in edges]})


def det(label, x, y, w=40, h=40, id=-1):
    return Detection(label, x, y, w, h, score=1.0, id=id)


def make_ctx(bushes=(), enemies=(), gas=(), screens=None, now=0.0):
    me = det("player", 100, 300, 10, 10)
    groups = {}
    for label, items in (("bush", bushes), ("enemy", enemies), ("gas", gas)):
        ds = []
        for i, (x, y, w) in enumerate(items):
            ds.append(det(label, x, y, w, w, id=i + 1))
        if ds:
            groups[label] = LabelGroup(label, ds)
    return Ctx(groups, me, now, W, H, screens=screens or ScreenState())


SCREEN = ScreenState(current="title", name="タイトル", score=0.95,
                     stable=True, scores={"title": 0.95, "lobby": 0.1})


def test_screen_is_gates_a_click():
    gr = g([("s", "screen_is", {"screen": "title"}),
            ("c", "click_at", {"x": 0.5, "y": 0.8, "cooldown": 0})],
           [("s", "result", "c", "run")])
    c = make_ctx(screens=SCREEN)
    st = gr.tick(c)
    assert st["s"]["text"].startswith("タイトル")
    assert c.intents.clicks == [(480.0, 432.0)]

    c2 = make_ctx(screens=ScreenState(current="lobby", name="ロビー", scores={"lobby": 0.9}))
    gr.tick(c2)
    assert c2.intents.clicks == []


def test_wait_screen_succeeds_and_times_out():
    gr = g([("w", "wait_screen", {"screen": "lobby", "timeout": 2})])
    c = make_ctx(screens=ScreenState(current="title"), now=0.0)
    assert gr.tick(c)["w"]["text"] == "待機"
    c2 = make_ctx(screens=ScreenState(current="lobby"), now=0.1)
    st = gr.tick(c2)
    assert st["w"]["text"] == "完了"

    gr2 = g([("w", "wait_screen", {"screen": "never", "timeout": 1})])
    gr2.tick(make_ctx(now=0.0))
    st = gr2.tick(make_ctx(now=1.5))
    assert st["w"]["text"] == "タイムアウト"


def test_screen_changed_is_true_only_on_the_edge():
    gr = g([("c", "screen_changed", {})])
    assert gr.tick(make_ctx(screens=ScreenState(current="a")))["c"]["text"].startswith("不明")
    assert "変わった" in gr.tick(make_ctx(screens=ScreenState(current="b")))["c"]["text"]


def test_hold_key_presses_until_input_stops():
    gr = g([("s", "screen_is", {"screen": "match"}),
            ("h", "hold_key", {"key": "e", "seconds": 0})],
           [("s", "result", "h", "run")])
    c = make_ctx(screens=ScreenState(current="match"))
    gr.tick(c)
    assert c.intents.key_downs == ["e"]
    c2 = make_ctx(screens=ScreenState(current="match"))
    gr.tick(c2)
    assert c2.intents.key_downs == [] and c2.intents.key_ups == []

    c3 = make_ctx(screens=ScreenState(current="other"))     # run false -> release
    gr.tick(c3)
    assert c3.intents.key_ups == ["e"]


def test_hold_key_releases_after_seconds():
    gr = g([("h", "hold_key", {"key": "w", "seconds": 0.2})])
    gr.tick(make_ctx(now=0.0))
    st = gr.tick(make_ctx(now=0.25))
    assert st["h"]["text"].startswith("解放")


def test_release_key_fires_once():
    gr = g([("r", "release_key", {"key": "space"})])
    c = make_ctx()
    gr.tick(c)
    assert c.intents.key_ups == ["space"]
    c2 = make_ctx()
    gr.tick(c2)
    assert c2.intents.key_ups == []


def test_wait_node_gates_for_seconds():
    gr = g([("w", "wait", {"seconds": 1.0})])
    c = make_ctx(now=0.0)
    assert gr.tick(c)["w"]["text"] == "1.0s"
    st = gr.tick(make_ctx(now=0.5))
    assert st["w"]["text"] == "0.5s"
    st = gr.tick(make_ctx(now=1.0))
    assert st["w"]["text"] == "完了"


def test_tactics_moves_and_attacks_with_a_key():
    gr = g([("t", "tactics", {"bush": "bush", "enemy": "enemy", "gas": "",
                              "attack_mode": "key", "attack_key": "f",
                              "priority": 2})])
    c = make_ctx(bushes=[(700, 300, 80)], enemies=[(400, 300, 20)])
    st = gr.tick(c)
    assert c.intents.move is not None and c.intents.move[0] > 0.5
    assert c.intents.taps == ["f"]
    assert "attack" in st["t"]["text"] or "発" not in st["t"]["text"]
    assert st["t"]["active"] is True


def test_tactics_can_attack_by_clicking_the_target():
    gr = g([("t", "tactics", {"bush": "bush", "enemy": "enemy", "gas": "",
                              "attack_mode": "click", "attack_key": "f"})])
    c = make_ctx(bushes=[(700, 300, 80)], enemies=[(400, 300, 20)])
    gr.tick(c)
    assert c.intents.clicks == [(400, 300)]
    assert c.intents.taps == []


def test_tactics_prioritises_gas_safety():
    gr = g([("t", "tactics", {"bush": "bush", "enemy": "enemy", "gas": "gas",
                              "attack_mode": "key", "attack_key": "f"})])
    # standing inside gas, with a safe bush to the right
    c = make_ctx(bushes=[(750, 300, 80)], gas=[(100, 300, 120)])
    st = gr.tick(c)
    assert "rotate" in st["t"]["text"]
    assert c.intents.move is not None and c.intents.move[0] > 0.2


def test_wait_screen_accepts_the_screen_name():
    gr = g([("w", "wait_screen", {"screen": "lobby", "timeout": 5})])
    state = ScreenState(current="s_1", name="lobby", score=0.95,
                        scores={"s_1": 0.95},
                        candidates=[{"id": "s_1", "name": "lobby", "score": 0.95, "current": True}])
    st = gr.tick(make_ctx(screens=state))
    assert st["w"]["text"] == "完了"


def test_release_holds_forgets_pressed_keys():
    gr = g([("h", "hold_key", {"key": "e", "seconds": 0})])
    gr.tick(make_ctx())
    gr.release_holds()
    c = make_ctx()
    gr.tick(c)
    assert c.intents.key_downs == ["e"]      # pressed again after a stop


def test_tactics_does_nothing_when_run_is_false():
    gr = g([("s", "screen_is", {"screen": "match"}),
            ("t", "tactics", {"bush": "bush", "enemy": "enemy", "gas": ""})],
           [("s", "result", "t", "run")])
    c = make_ctx(bushes=[(700, 300, 80)], enemies=[(400, 300, 20)],
                 screens=ScreenState(current="menu"))
    st = gr.tick(c)
    assert c.intents.move is None and c.intents.taps == []
    assert st["t"]["text"] == "停止"
