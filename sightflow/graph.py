"""Node-graph engine for the web editor (Node-RED / Scratch style).

A graph is evaluated once per frame. Data flows left to right along wires:

    [Objects: enemy] --list--> [Nearest] --object--> [Aim at]
                                   |                     ^
                                   +--> [Distance] --> [<  300] --bool--+ (run)

Wire types: list (of detections), object (one detection), number, bool, any.
Every node type is declared once in NODE_TYPES; the web UI builds its palette
from GET /api/node-types, so adding a node here makes it appear in the editor.

Inputs left unconnected fall back to the node's property of the same name
(e.g. Compare.b), or to a neutral default (an action's `run` = true).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable

from .detection import Detection, missing
from .flow import Intents
from .behavior import GasField, PolicyConfig, TacticalPolicy
from .screens import ScreenState

# ============================================================== declarations
CATEGORIES = {
    "screen": {"title": "画面", "color": "#22d3ee"},
    "sense": {"title": "見る", "color": "#3fa7ff"},
    "pick": {"title": "選ぶ", "color": "#9b7bff"},
    "logic": {"title": "判断", "color": "#ffb020"},
    "act": {"title": "操作", "color": "#ff5a6e"},
    "util": {"title": "便利", "color": "#8a94a6"},
}

FIELDS = ["distance", "x", "y", "w", "h", "id", "score", "angle", "exists"]

IO_NAMES = {
    "list": "リスト", "object": "物体", "count": "数", "any": "ある", "run": "実行", "target": "対象",
    "a": "a", "b": "b", "result": "結果", "value": "値", "in": "入力", "out": "出力", "me": "自分",
    "found": "見つかった", "speed": "速度", "stuck_time": "引っかかり秒", "stuck": "引っかかり", "t": "秒",
    "radius": "半径", "id": "ID", "set": "記憶する", "switched": "切り替えた",
    "score": "一致度", "changed": "変わった", "done": "完了", "failed": "失敗", "attack": "攻撃",
}
TYPE_NAMES = {"list": "リスト", "object": "物体", "number": "数", "bool": "はい/いいえ", "any": "なんでも"}


@dataclass
class NodeType:
    type: str
    category: str
    title: str
    desc: str
    inputs: list[tuple[str, str]] = field(default_factory=list)   # (name, wire type)
    outputs: list[tuple[str, str]] = field(default_factory=list)
    props: list[dict] = field(default_factory=list)
    fn: Callable | None = None

    def to_json(self):
        return {"type": self.type, "category": self.category, "title": self.title,
                "desc": self.desc,
                "inputs": [{"name": n, "type": t} for n, t in self.inputs],
                "outputs": [{"name": n, "type": t} for n, t in self.outputs],
                "props": self.props}


NODE_TYPES: dict[str, NodeType] = {}


def node(type_, category, title, desc, inputs=(), outputs=(), props=()):
    def deco(fn):
        NODE_TYPES[type_] = NodeType(type_, category, title, desc, list(inputs),
                                     list(outputs), list(props), fn)
        return fn
    return deco


def P(name, kind, default=None, label=None, **extra):
    d = {"name": name, "kind": kind, "default": default, "label": label or name}
    d.update(extra)
    return d


# ============================================================== runtime context
class Ctx:
    """Everything a node can see during one tick."""

    def __init__(self, groups, me, now, frame_w, frame_h, motion=None, screens=None):
        self.groups = groups
        self.me = me
        self.now = now
        self.frame_w = frame_w
        self.frame_h = frame_h
        self.motion = motion or {}
        self.screens = screens or ScreenState()
        self.intents = Intents()
        self.moves: list[tuple[float, tuple[float, float], str]] = []  # (priority, vec, node)
        self.active: set[str] = set()   # action nodes that fired this tick

    def point(self, x, y):
        if 0 <= x <= 1 and 0 <= y <= 1:
            return x * self.frame_w, y * self.frame_h
        return x, y


def _num(v, default=0.0):
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _unit(vx, vy):
    n = math.hypot(vx, vy)
    return None if n < 1e-6 else (vx / n, vy / n)


def _ok(d):
    return isinstance(d, Detection) and d.exists


# ============================================================== SENSE
@node("objects", "sense", "物体", "検出されたラベルの物体をすべて出力（近い順）",
      outputs=[("list", "list"), ("count", "number"), ("any", "bool")],
      props=[P("label", "label", "", "ラベル")])
def _objects(ctx, i, p, s):
    items = list(ctx.groups.get(p["label"], []).items) if p["label"] in ctx.groups else []
    return {"list": items, "count": len(items), "any": bool(items)}, f"{len(items)}個"


@node("me", "sense", "自分", "基準になる自分の位置（設定の『自分のラベル』）",
      outputs=[("me", "object"), ("found", "bool")])
def _me(ctx, i, p, s):
    return {"me": ctx.me, "found": ctx.me.exists}, "見えている" if ctx.me.exists else "見えない"


@node("motion", "sense", "動き", "自分の実際の移動速度と、壁などに引っかかっているか",
      outputs=[("speed", "number"), ("stuck_time", "number"), ("stuck", "bool")],
      props=[P("after", "number", 0.4, "引っかかり判定(秒)")])
def _motion(ctx, i, p, s):
    st = _num(ctx.motion.get("stuck_time"))
    stuck = st > _num(p["after"], 0.4)
    return ({"speed": _num(ctx.motion.get("speed")), "stuck_time": st, "stuck": stuck},
            "引っかかり中" if stuck else f"{_num(ctx.motion.get('speed')):.0f}px/s")


@node("clock", "sense", "時間", "開始からの経過秒数", outputs=[("t", "number")])
def _clock(ctx, i, p, s):
    return {"t": ctx.now}, f"{ctx.now:.1f}s"


# ============================================================== SCREEN
def _screen_lookup(ctx, want: str) -> tuple[bool, float]:
    """A node may refer to a screen by id or by name (names survive re-registration)."""
    st = ctx.screens
    if not want:
        return False, st.score
    if st.current == want or (st.name and st.name == want):
        return True, st.score
    score = st.scores.get(want)
    if score is None:
        for c in st.candidates:
            if want in (c.get("id"), c.get("name")):
                score = c.get("score")
                break
    return False, float(score or 0.0)


@node("screen_is", "screen", "画面が", "今の画面が指定した画面なら true",
      outputs=[("result", "bool"), ("score", "number")],
      props=[P("screen", "screen", "", "画面")])
def _screen_is(ctx, i, p, s):
    hit, score = _screen_lookup(ctx, str(p["screen"]))
    name = ctx.screens.name or "不明"
    return {"result": hit, "score": score}, f"{name} {score:.2f}"


@node("screen_score", "screen", "画面の一致度", "指定した画面（空欄なら今の画面）の一致度",
      outputs=[("value", "number")],
      props=[P("screen", "screen", "", "画面")])
def _screen_score(ctx, i, p, s):
    _, score = _screen_lookup(ctx, str(p["screen"]))
    return {"value": score}, f"{score:.2f}"


@node("screen_changed", "screen", "画面が変わった", "前のティックから画面が変わった瞬間に true",
      outputs=[("changed", "bool")])
def _screen_changed(ctx, i, p, s):
    cur = ctx.screens.current
    changed = ("last" in s) and s["last"] != cur
    s["last"] = cur
    return {"changed": changed}, (ctx.screens.name or "不明") + ("（変わった）" if changed else "")


@node("wait_screen", "screen", "画面を待つ", "指定した画面になるまで待つ。タイムアウトで失敗",
      inputs=[("run", "bool")],
      outputs=[("done", "bool"), ("failed", "bool")],
      props=[P("screen", "screen", "", "画面"), P("timeout", "number", 10, "タイムアウト(秒)")])
def _wait_screen(ctx, i, p, s):
    sid = str(p["screen"])
    if not _run(i):
        s.clear()
        return {"done": False, "failed": False}, "待機"
    if "start" not in s:
        s["start"] = ctx.now
    if _screen_lookup(ctx, sid)[0]:
        s["done"], s["failed"] = True, False
    elif not s.get("done") and ctx.now - s["start"] >= _num(p["timeout"], 10):
        s["failed"] = True
    done, failed = bool(s.get("done")), bool(s.get("failed"))
    return {"done": done, "failed": failed}, ("完了" if done else "タイムアウト" if failed else "待機")


# ============================================================== PICK
@node("nearest", "pick", "いちばん近い", "リストから自分にいちばん近い物体",
      inputs=[("list", "list")], outputs=[("object", "object"), ("found", "bool")])
def _nearest(ctx, i, p, s):
    lst = i.get("list") or []
    d = lst[0] if lst else missing("?")
    return {"object": d, "found": d.exists}, _desc(d)


@node("farthest", "pick", "いちばん遠い", "リストから自分にいちばん遠い物体",
      inputs=[("list", "list")], outputs=[("object", "object"), ("found", "bool")])
def _farthest(ctx, i, p, s):
    lst = i.get("list") or []
    d = lst[-1] if lst else missing("?")
    return {"object": d, "found": d.exists}, _desc(d)


@node("within", "pick", "範囲内", "自分から半径以内の物体だけ残す",
      inputs=[("list", "list"), ("radius", "number")], outputs=[("list", "list"), ("count", "number")],
      props=[P("radius", "number", 250, "半径(px)")])
def _within(ctx, i, p, s):
    r = _num(i.get("radius", p["radius"]), 250)
    lst = [d for d in (i.get("list") or []) if d.distance < r]
    return {"list": lst, "count": len(lst)}, f"{len(lst)}個"


@node("by_id", "pick", "IDで探す", "追跡IDが一致する物体（同じ敵を追い続ける）",
      inputs=[("list", "list"), ("id", "number")], outputs=[("object", "object"), ("found", "bool")])
def _by_id(ctx, i, p, s):
    want = int(_num(i.get("id"), -1))
    d = next((d for d in (i.get("list") or []) if d.id == want), missing("?"))
    return {"object": d, "found": d.exists}, _desc(d)


@node("lock_on", "pick", "ロックオン", "一度選んだ物体を追跡IDで追い続ける。見失ったら一番近いものに乗り換え",
      inputs=[("list", "list")], outputs=[("object", "object"), ("found", "bool"), ("switched", "bool")],
      props=[P("grace", "number", 0.5, "見失い猶予(秒)")])
def _lock_on(ctx, i, p, s):
    lst = i.get("list") or []
    cur = next((d for d in lst if d.id == s.get("id")), None) if "id" in s else None
    switched = False
    if cur is not None:
        s["seen"] = ctx.now
    elif lst and ctx.now - s.get("seen", -math.inf) >= _num(p["grace"], 0.5):
        cur = lst[0]                      # nearest
        switched = s.get("id") != cur.id
        s["id"], s["seen"] = cur.id, ctx.now
    d = cur if cur is not None else missing("?")
    return {"object": d, "found": d.exists, "switched": switched}, \
        (f"ロック中 {_desc(d)}" if d.exists else ("見失い中" if "id" in s else "なし"))


@node("field", "pick", "値を取り出す", "物体の距離・座標・ID などを数値で取り出す",
      inputs=[("object", "object")], outputs=[("value", "number")],
      props=[P("field", "select", "distance", "項目", options=FIELDS)])
def _field(ctx, i, p, s):
    d = i.get("object")
    f = p["field"]
    if not isinstance(d, Detection):
        return {"value": math.inf if f == "distance" else 0}, "-"
    v = getattr(d, f)
    v = float(v) if not isinstance(v, bool) else (1.0 if v else 0.0)
    return {"value": v}, "∞" if v == math.inf else f"{v:.0f}"


# ============================================================== LOGIC
_OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
        ">=": lambda a, b: a >= b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b}


@node("compare", "logic", "比べる", "a と b を比べて true / false",
      inputs=[("a", "number"), ("b", "number")], outputs=[("result", "bool")],
      props=[P("op", "select", "<", "比較", options=list(_OPS)), P("b", "number", 200, "b")])
def _compare(ctx, i, p, s):
    a, b = _num(i.get("a"), math.inf), _num(i.get("b", p["b"]))
    r = _OPS.get(p["op"], _OPS["<"])(a, b)
    return {"result": r}, "true" if r else "false"


@node("and", "logic", "かつ", "両方 true なら true",
      inputs=[("a", "bool"), ("b", "bool")], outputs=[("result", "bool")])
def _and(ctx, i, p, s):
    r = bool(i.get("a", True)) and bool(i.get("b", True))
    return {"result": r}, "true" if r else "false"


@node("or", "logic", "または", "どちらか true なら true",
      inputs=[("a", "bool"), ("b", "bool")], outputs=[("result", "bool")])
def _or(ctx, i, p, s):
    r = bool(i.get("a", False)) or bool(i.get("b", False))
    return {"result": r}, "true" if r else "false"


@node("not", "logic", "ではない", "true と false を反転",
      inputs=[("in", "bool")], outputs=[("result", "bool")])
def _not(ctx, i, p, s):
    r = not bool(i.get("in", False))
    return {"result": r}, "true" if r else "false"


@node("number", "logic", "数", "決まった数値", outputs=[("value", "number")],
      props=[P("value", "number", 100, "値")])
def _number(ctx, i, p, s):
    v = _num(p["value"])
    return {"value": v}, f"{v:g}"


_MATH = {"+": lambda a, b: a + b, "-": lambda a, b: a - b, "×": lambda a, b: a * b,
         "÷": lambda a, b: a / b if b else 0.0}


@node("math", "logic", "計算", "a と b を計算",
      inputs=[("a", "number"), ("b", "number")], outputs=[("value", "number")],
      props=[P("op", "select", "+", "演算", options=list(_MATH)), P("b", "number", 1, "b")])
def _math(ctx, i, p, s):
    v = _MATH.get(p["op"], _MATH["+"])(_num(i.get("a")), _num(i.get("b", p["b"])))
    return {"value": v}, f"{v:g}"


@node("cooldown", "logic", "間隔をあける", "true が続いても、指定秒ごとに1回だけ true を出す",
      inputs=[("in", "bool")], outputs=[("out", "bool")],
      props=[P("seconds", "number", 0.5, "間隔(秒)")])
def _cooldown(ctx, i, p, s):
    fire = bool(i.get("in", False)) and ctx.now - s.get("last", -math.inf) >= _num(p["seconds"], 0.5)
    if fire:
        s["last"] = ctx.now
    return {"out": fire}, "発火" if fire else "待機"


@node("hold_true", "logic", "しばらく続ける", "一度 true になったら指定秒 true を保つ",
      inputs=[("in", "bool")], outputs=[("out", "bool")],
      props=[P("seconds", "number", 0.6, "保つ秒数")])
def _hold_true(ctx, i, p, s):
    if bool(i.get("in", False)):
        s["until"] = ctx.now + _num(p["seconds"], 0.6)
    on = ctx.now < s.get("until", -math.inf)
    return {"out": on}, "true" if on else "false"


@node("wait", "logic", "待つ", "指定した秒数がたつまで待つ（待機中は false）",
      inputs=[("run", "bool")], outputs=[("out", "bool")],
      props=[P("seconds", "number", 1.0, "秒数")])
def _wait(ctx, i, p, s):
    if not _run(i):
        s.pop("start", None)
        return {"out": False}, "待機"
    if "start" not in s:
        s["start"] = ctx.now
    remain = max(0.0, _num(p["seconds"], 1.0) - (ctx.now - s["start"]))
    return {"out": remain <= 0}, ("完了" if remain <= 0 else f"{remain:.1f}s")


# ============================================================== UTIL
@node("remember", "util", "覚える", "set が true のとき value を記憶し、以後ずっと出力（例: 狙う敵のID）",
      inputs=[("value", "any"), ("set", "bool")], outputs=[("value", "any")])
def _remember(ctx, i, p, s):
    if bool(i.get("set", False)):
        v = i.get("value")
        s["v"] = v.id if isinstance(v, Detection) else v
    v = s.get("v")
    return {"value": v}, "-" if v is None else str(v)


@node("debug", "util", "表示", "流れてきた値をノード上に表示", inputs=[("in", "any")])
def _debug(ctx, i, p, s):
    v = i.get("in")
    if isinstance(v, Detection):
        txt = _desc(v)
    elif isinstance(v, list):
        txt = f"{len(v)}個: " + ", ".join(f"{d.label}#{d.id}" for d in v[:4])
    elif isinstance(v, float):
        txt = "∞" if v == math.inf else f"{v:.1f}"
    else:
        txt = str(v)
    return {}, txt


# ============================================================== ACT
def _run(i):
    return bool(i.get("run", True))


@node("move_toward", "act", "近づく", "対象に向かって移動（同時に複数あれば優先度が高いもの）",
      inputs=[("run", "bool"), ("target", "object")],
      props=[P("priority", "number", 1, "優先度")])
def _move_toward(ctx, i, p, s):
    return _move_rel(ctx, i, p, +1)


@node("move_away", "act", "離れる", "対象から離れるように移動",
      inputs=[("run", "bool"), ("target", "object")],
      props=[P("priority", "number", 2, "優先度")])
def _move_away(ctx, i, p, s):
    return _move_rel(ctx, i, p, -1)


def _move_rel(ctx, i, p, sign):
    t = i.get("target")
    if not _run(i) or not _ok(t):
        return {}, "待機"
    u = _unit(t.x - ctx.me.x, t.y - ctx.me.y)
    if u is None:
        return {}, "到着"
    ctx.moves.append((_num(p["priority"], 1), (sign * u[0], sign * u[1]), p["_id"]))
    return {}, f"→ {t.label}#{t.id}"


@node("move_dir", "act", "方向に移動", "決まった方向へ移動（上=0,-1 / 右=1,0）",
      inputs=[("run", "bool")],
      props=[P("dx", "number", 0, "x"), P("dy", "number", -1, "y"), P("priority", "number", 0, "優先度")])
def _move_dir(ctx, i, p, s):
    u = _unit(_num(p["dx"]), _num(p["dy"]))
    if not _run(i) or u is None:
        return {}, "待機"
    ctx.moves.append((_num(p["priority"]), u, p["_id"]))
    return {}, "移動"


@node("dodge", "act", "横によける", "引っかかった時用: 直前の移動方向の真横へ動く",
      inputs=[("run", "bool")], props=[P("priority", "number", 9, "優先度")])
def _dodge(ctx, i, p, s):
    lm = ctx.motion.get("last_move")
    if not _run(i):
        s.pop("dir", None)
        return {}, "待機"
    if "dir" not in s and lm is not None and lm.any:
        s["dir"] = (-lm.y, lm.x)
    if "dir" not in s:
        return {}, "待機"
    ctx.moves.append((_num(p["priority"], 9), s["dir"], p["_id"]))
    return {}, "よけ中"


@node("aim", "act", "狙って撃つ", "攻撃ボタンから対象の方向へドラッグ（タッチ操作の照準）",
      inputs=[("run", "bool"), ("target", "object")],
      props=[P("bx", "number", 0.88, "ボタンx(0-1)"), P("by", "number", 0.78, "ボタンy(0-1)"),
             P("length", "number", 120, "ドラッグ長"), P("cooldown", "number", 0.4, "間隔(秒)")])
def _aim(ctx, i, p, s):
    t = i.get("target")
    if not _run(i) or not _ok(t) or ctx.now - s.get("last", -math.inf) < _num(p["cooldown"]):
        return {}, "待機"
    u = _unit(t.x - ctx.me.x, t.y - ctx.me.y)
    if u is None:
        return {}, "待機"
    bx, by = ctx.point(_num(p["bx"]), _num(p["by"]))
    L = _num(p["length"], 120)
    ctx.intents.drags.append((bx, by, bx + u[0] * L, by + u[1] * L, 0.15))
    s["last"] = ctx.now
    return {}, f"発射 → {t.label}#{t.id}"


@node("click", "act", "クリック", "対象の位置をクリック",
      inputs=[("run", "bool"), ("target", "object")],
      props=[P("cooldown", "number", 0.3, "間隔(秒)")])
def _click(ctx, i, p, s):
    t = i.get("target")
    if not _run(i) or not _ok(t) or ctx.now - s.get("last", -math.inf) < _num(p["cooldown"]):
        return {}, "待機"
    ctx.intents.clicks.append((t.x, t.y))
    s["last"] = ctx.now
    return {}, f"クリック {t.label}#{t.id}"


@node("click_at", "act", "画面座標をクリック", "画面上の決まった位置をクリック（0〜1は割合、それ以上はピクセル）",
      inputs=[("run", "bool")],
      props=[P("x", "number", 0.5, "x(0-1)"), P("y", "number", 0.5, "y(0-1)"),
             P("cooldown", "number", 1.0, "間隔(秒)")])
def _click_at(ctx, i, p, s):
    if not _run(i) or ctx.now - s.get("last", -math.inf) < _num(p["cooldown"]):
        return {}, "待機"
    ctx.intents.clicks.append(ctx.point(_num(p["x"]), _num(p["y"])))
    s["last"] = ctx.now
    return {}, "クリック"


@node("key", "act", "キーを押す", "キーを1回押す",
      inputs=[("run", "bool")],
      props=[P("key", "text", "space", "キー"), P("cooldown", "number", 0.5, "間隔(秒)")])
def _key(ctx, i, p, s):
    if not _run(i) or ctx.now - s.get("last", -math.inf) < _num(p["cooldown"]):
        return {}, "待機"
    ctx.intents.taps.append(str(p["key"]))
    s["last"] = ctx.now
    return {}, f"押す {p['key']}"


@node("hold_key", "act", "キーを押し続ける", "キーを押したままにする。秒数を過ぎるか実行が false で離す",
      inputs=[("run", "bool")],
      props=[P("key", "text", "space", "キー"), P("seconds", "number", 0, "秒数(0=実行中ずっと)")])
def _hold_key(ctx, i, p, s):
    key = str(p["key"])
    seconds = _num(p["seconds"], 0)
    run = _run(i)
    if s.get("key") and s["key"] != key:
        ctx.intents.key_ups.append(s["key"])
        s.clear()
    if not run:
        if s.get("down"):
            ctx.intents.key_ups.append(key)
        s.clear()
        return {}, "待機"
    if not s.get("down"):
        ctx.intents.key_downs.append(key)
        s.update(down=True, key=key,
                 until=(ctx.now + seconds if seconds > 0 else None))
    if s.get("until") is not None and ctx.now >= s["until"]:
        ctx.intents.key_ups.append(key)
        s.update(down=False, until=None)
        return {}, f"解放 {key}"
    return {}, f"押下 {key}"


@node("release_key", "act", "キーを離す", "押しっぱなしにしているキーを離す",
      inputs=[("run", "bool")],
      props=[P("key", "text", "space", "キー")])
def _release_key(ctx, i, p, s):
    key = str(p["key"])
    if not _run(i):
        s.pop("done", None)
        return {}, "待機"
    if not s.get("done"):
        ctx.intents.key_ups.append(key)
        s["done"] = True
    return {}, f"離す {key}"


@node("tactics", "act", "戦術（草むら/敵/ガス）",
      "草むらへ隠れ、敵が来たら攻撃し、ガスが迫ったら安全な草むらへ移る（移動と攻撃は同時）",
      inputs=[("run", "bool")], outputs=[("attack", "bool")],
      props=[P("bush", "label", "bush", "草むらラベル"),
             P("enemy", "label", "enemy", "敵ラベル"),
             P("gas", "label", "", "ガスラベル"),
             P("attack_mode", "select", "key", "攻撃方法", options=["key", "click"]),
             P("attack_key", "text", "space", "攻撃キー"),
             P("enemy_range", "number", 320, "攻撃射程(px)"),
             P("attack_cooldown", "number", 0.35, "攻撃間隔(秒)"),
             P("gas_margin", "number", 80, "ガス余裕(px)"),
             P("gas_danger", "number", 160, "危険距離(px)"),
             P("priority", "number", 2, "優先度")])
def _tactics(ctx, i, p, s):
    if not _run(i):
        return {"attack": False}, "停止"
    sig = (_num(p["enemy_range"], 320), _num(p["attack_cooldown"], 0.35),
           _num(p["gas_margin"], 80), _num(p["gas_danger"], 160))
    if s.get("sig") != sig or "policy" not in s:
        s["policy"] = TacticalPolicy(PolicyConfig(
            enemy_range=sig[0], attack_cooldown=sig[1],
            gas_margin=sig[2], gas_danger=sig[3]))
        s["sig"] = sig

    def items(label):
        return list(ctx.groups[label].items) if label and label in ctx.groups else []

    bushes = items(str(p["bush"]))
    enemies = items(str(p["enemy"]))
    gas = GasField(items(str(p["gas"])), frame_size=(ctx.frame_w, ctx.frame_h))
    dec = s["policy"].tick(ctx.now, ctx.me, bushes, enemies, gas,
                           ctx.frame_w, ctx.frame_h)
    if dec.move:
        ctx.moves.append((_num(p["priority"], 2), dec.move, p["_id"]))
    if dec.attack:
        if str(p["attack_mode"]) == "click" and dec.attack_target is not None:
            ctx.intents.clicks.append((dec.attack_target.x, dec.attack_target.y))
        else:
            ctx.intents.taps.append(str(p["attack_key"]))
    clear = "∞" if dec.clearance == math.inf else f"{dec.clearance:.0f}"
    tgt = f"→#{getattr(dec.target, 'id', '?')}" if dec.target is not None else ""
    return {"attack": bool(dec.attack)}, f"{dec.mode}{tgt} / {dec.reason} / 余裕{clear}px"


def _desc(d):
    if not isinstance(d, Detection) or not d.exists:
        return "なし"
    dist = "" if d.distance == math.inf else f" {d.distance:.0f}px"
    return f"{d.label}#{d.id}{dist}"


# ============================================================== graph runtime
class GraphError(ValueError):
    pass


class Graph:
    """Compiled graph. `nodes`/`edges` use the React Flow JSON shape:
    node {id, type, data: {props...}}, edge {source, sourceHandle, target, targetHandle}."""

    def __init__(self, spec: dict):
        self.nodes: dict[str, dict] = {}
        for n in spec.get("nodes", []):
            t = n.get("type")
            if t not in NODE_TYPES:
                raise GraphError(f"unknown node type {t!r}")
            nt = NODE_TYPES[t]
            data = n.get("data") or {}
            props = {pp["name"]: data.get(pp["name"], pp["default"]) for pp in nt.props}
            props["_id"] = n["id"]
            self.nodes[n["id"]] = {"type": nt, "props": props}
        self.inputs: dict[str, dict[str, tuple[str, str]]] = {nid: {} for nid in self.nodes}
        for e in spec.get("edges", []):
            if e["source"] in self.nodes and e["target"] in self.nodes:
                self.inputs[e["target"]][e.get("targetHandle")] = (e["source"], e.get("sourceHandle"))
        self.order = self._toposort()
        self.state: dict[str, dict] = {nid: {} for nid in self.nodes}

    def _toposort(self):
        order, mark = [], {}

        def visit(nid, path):
            if mark.get(nid) == 2:
                return
            if mark.get(nid) == 1:
                raise GraphError("ループしている配線があります: " + " → ".join(path + [nid]))
            mark[nid] = 1
            for src, _ in self.inputs[nid].values():
                visit(src, path + [nid])
            mark[nid] = 2
            order.append(nid)

        for nid in self.nodes:
            visit(nid, [])
        return order

    def labels_used(self) -> set[str]:
        return {n["props"].get("label") for n in self.nodes.values()
                if n["type"].type == "objects"} - {None, ""}

    def release_holds(self):
        """Called when input stops: forget held keys so a later live run presses again."""
        for nid, n in self.nodes.items():
            if n["type"].type == "hold_key":
                self.state[nid]["down"] = False
                self.state[nid].pop("until", None)

    def tick(self, ctx: Ctx) -> dict[str, dict]:
        """Runs every node once. Returns per-node status for the UI:
        {node_id: {"text": str, "active": bool, "error": str|None}}."""
        outputs: dict[str, dict] = {}
        status: dict[str, dict] = {}
        for nid in self.order:
            n = self.nodes[nid]
            ins = {}
            for name, (src, handle) in self.inputs[nid].items():
                if src in outputs and handle in outputs[src]:
                    ins[name] = outputs[src][handle]
            try:
                out, text = n["type"].fn(ctx, ins, n["props"], self.state[nid])
                outputs[nid] = out
                active = n["type"].category == "act" and text not in ("待機", "到着")
                status[nid] = {"text": text, "active": active, "error": None}
            except Exception as e:  # one bad node must not stop the bot
                outputs[nid] = {}
                status[nid] = {"text": "エラー", "active": False, "error": str(e)}
        if ctx.moves:
            prio, vec, winner = max(ctx.moves, key=lambda m: m[0])
            ctx.intents.move = vec
            for _, _, nid in ctx.moves:
                if nid != winner:
                    status[nid]["active"] = False
                    status[nid]["text"] += "（優先度で負け）"
        for nid, st in status.items():
            if st["active"]:
                ctx.active.add(nid)
        return status


def node_types_json():
    return {"categories": CATEGORIES, "io_names": IO_NAMES, "type_names": TYPE_NAMES,
            "types": [t.to_json() for t in NODE_TYPES.values()]}


def nodes_markdown() -> str:
    """Node reference for docs/nodes.md, generated from NODE_TYPES so it never
    drifts from the code (a test checks the file is up to date)."""
    out = ["# ノード一覧", "",
           "> このファイルは `python -m sightflow nodes-doc > docs/nodes.md` で自動生成しています。",
           "> 手で編集せず、`sightflow/graph.py` のノード定義を変えてから作り直してください。", "",
           "線（データ）の種類: " + " / ".join(f"**{v}**" for v in TYPE_NAMES.values()) + "。",
           "入力が未接続のときは、同じ名前の設定値（あれば）か、既定値（操作ノードの「実行」は常に はい）が使われます。", ""]
    for key, cat in CATEGORIES.items():
        out += [f"## {cat['title']}", ""]
        for t in (t for t in NODE_TYPES.values() if t.category == key):
            out += [f"### {t.title}  `{t.type}`", "", t.desc, ""]
            if t.inputs:
                out.append("- 入力: " + "、".join(f"{IO_NAMES.get(n, n)}（{TYPE_NAMES[ty]}）" for n, ty in t.inputs))
            if t.outputs:
                out.append("- 出力: " + "、".join(f"{IO_NAMES.get(n, n)}（{TYPE_NAMES[ty]}）" for n, ty in t.outputs))
            for pr in t.props:
                extra = f"（{' / '.join(pr['options'])}）" if pr.get("options") else ""
                dflt = "" if pr["default"] in (None, "") else f"、既定 `{pr['default']}`"
                out.append(f"- 設定「{pr['label']}」{extra}{dflt}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"
