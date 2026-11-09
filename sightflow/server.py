"""Web UI server: node editor + live view.

    python -m sightflow web [project.json] [--port 8765]

One background thread (Engine) runs capture -> detect+track -> graph -> input.
The browser edits the graph/settings over REST and watches a WebSocket stream
with the frame, detections and every node's live value.

Modes:
  stop    nothing runs
  watch   detect + evaluate the graph, but send NO input (safe preview)
  live    same, and actually press keys / move the mouse   (F12 = back to stop)
"""
from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
import traceback
from pathlib import Path

import cv2
from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .bot import build_groups
from .detection import to_dict
from .detectors import build_detectors
from .graph import Ctx, Graph, GraphError, node_types_json
from .io import KeyboardMouseInput, NullInput, build_source, list_windows
from .motion import MotionTracker
from .tracking import CentroidTracker

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "webui" / "dist"
DEFAULT_PROJECT = ROOT / "demo" / "arena_project.json"
PROJECTS = ROOT / "projects"


def detector_specs(det: dict, base_dir: Path | None = None) -> list[dict]:
    """Detector specs for the project's main detector plus any cut-out images."""
    specs = _main_detector_specs(det)
    for im in det.get("images", []) or []:
        if im.get("label") and im.get("image"):
            path = Path(im["image"])
            if base_dir is not None and not path.is_absolute():
                path = base_dir / path
            specs.append({"type": "template", "label": im["label"], "image": str(path),
                          "threshold": float(im.get("threshold", 0.8))})
    return specs


def _main_detector_specs(det: dict) -> list[dict]:
    kind = det.get("type", "yolo_world")
    common = {k: det[k] for k in ("conf", "imgsz", "device", "iou") if k in det}
    if kind == "yolo_world":
        classes = {c["label"]: c.get("prompt") or c["label"] for c in det.get("classes", []) if c.get("label")}
        return [{"type": "yolo_world", "model": det.get("model") or "yolov8s-worldv2.pt",
                 "classes": classes, **common}]
    if kind == "yolo":
        labels = {c["label"]: c.get("prompt") or c["label"] for c in det.get("classes", []) if c.get("label")}
        return [{"type": "yolo", "model": det.get("model") or "yolo11s.pt", "labels": labels or None, **common}]
    if kind == "color":
        return list(det.get("color", []))
    if kind == "none":
        return []
    raise ValueError(f"unknown detector type {kind!r}")


def new_project(name: str) -> dict:
    """An empty project: YOLO-World on a window, no nodes yet."""
    return {
        "name": name,
        "settings": {
            "source": {"type": "window", "title": ""},
            "detector": {"type": "yolo_world", "model": "yolov8s-worldv2.pt", "conf": 0.25, "imgsz": 640,
                         "classes": [{"label": "player", "prompt": "player character"},
                                     {"label": "enemy", "prompt": "enemy character"}]},
            "anchor": "player",
            "input": {"move_keys": {"up": "w", "down": "s", "left": "a", "right": "d"}},
            "fps": 15,
            "stuck_min_speed": 25,
        },
        "graph": {"nodes": [], "edges": []},
    }


def safe_project_name(name: str) -> str:
    """A file-name-safe stem from whatever the user typed (Japanese is fine)."""
    stem = "".join(c if (c.isalnum() or c in "-_ ") else "_" for c in (name or "").strip())
    return stem.strip(" .")[:60] or "project"


def list_projects(current: Path, known: set[Path] = frozenset()) -> list[dict]:
    """Projects in projects/, the demo, the current file, and any opened this session."""
    files = {p.resolve() for p in PROJECTS.glob("*.json")} if PROJECTS.exists() else set()
    files |= {p.resolve() for p in known}
    files.add(current.resolve())
    if DEFAULT_PROJECT.exists():
        files.add(DEFAULT_PROJECT.resolve())
    out = []
    for p in sorted(files, key=lambda x: x.name.lower()):
        try:
            name = json.loads(p.read_text(encoding="utf-8")).get("name") or p.stem
        except Exception:
            continue
        out.append({"file": str(p), "name": name, "current": p == current.resolve()})
    return out


def friendly_error(e: Exception) -> str:
    """Turn the common setup errors into a Japanese message that says what to do."""
    msg = str(e)
    if "ultralytics" in msg:
        return ("YOLO の部品が入っていません。コマンドで `pip install ultralytics` を実行するか、"
                "検出器を「色」か「画像だけ」にしてください")
    if "no visible window" in msg:
        return ("指定した名前のウィンドウが見つかりません。ゲームを起動してから、"
                "設定の「ウィンドウ」で一覧から選び直してください")
    if "Windows-only" in msg:
        return "ウィンドウの取り込みは Windows 専用です。設定で「画面の範囲」を選んでください"
    if isinstance(e, FileNotFoundError):
        return f"ファイルが見つかりません: {msg}"
    if "cannot read video" in msg:
        return "動画を読み込めませんでした。ファイルの場所と形式（.mp4 など）を確認してください"
    return f"{type(e).__name__}: {msg}"


def _labels_of(d) -> list[str]:
    if hasattr(d, "labels"):
        return list(d.labels)
    if hasattr(d, "map"):
        return list(d.map.values())
    return [d.label]


class Engine:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.known_paths: set[Path] = {project_path}
        self.project = json.loads(project_path.read_text(encoding="utf-8"))
        self.lock = threading.RLock()
        self.mode = "stop"
        self.status = "idle"        # idle | loading | running | error
        self.error: str | None = None
        self.graph_error: str | None = None
        self.labels: list[str] = []
        self.packet: tuple[int, str] | None = None
        self._seq = 0
        self.source = None
        self.detectors = []
        self.graph = None
        self._live_input = None
        self._sim_input = None
        self.tracker = CentroidTracker()
        self.motion = MotionTracker()
        self.last_frame = None
        self.recorder = None
        self.recorded: list[dict] | None = None
        self.set_graph(self.project.get("graph", {"nodes": [], "edges": []}), save=False)
        threading.Thread(target=self._load_settings, daemon=True).start()
        threading.Thread(target=self._loop, daemon=True).start()
        self._start_stop_key()

    # ------------------------------------------------------------- project
    def save(self):
        self.project_path.write_text(json.dumps(self.project, ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    def switch_project(self, path: Path):
        """Open another project file (mode goes back to stop)."""
        project = json.loads(path.read_text(encoding="utf-8"))
        with self.lock:
            self.mode = "stop"
            if self._live_input:
                self._live_input.set_move(None)
            self.project_path = path
            self.known_paths.add(path)
            self.project = project
            self.graph = None
        self.set_graph(project.get("graph", {"nodes": [], "edges": []}), save=False)
        threading.Thread(target=self._load_settings, daemon=True).start()

    # ------------------------------------------------------------- record (FRep-style)
    def grab_frame(self):
        if self.last_frame is None and self.source is not None:
            with self.lock:
                self.last_frame = self.source.grab()
        return self.last_frame

    def start_recording(self):
        from .recorder import Recorder

        if self.status != "running" or self.source is None:
            raise RuntimeError("画面の取り込みが準備できていません（設定を確認してください）")
        self.set_mode("stop")
        frame = self.grab_frame()
        h, w = frame.shape[:2]
        rec = Recorder(self.source.origin, (w, h))
        rec.start(own_keyboard=not getattr(self, "_has_key_listener", False))
        self.recorder, self.recorded = rec, None

    def stop_recording(self) -> list[dict]:
        if self.recorder is not None:
            self.recorded = self.recorder.stop()
            self.recorder = None
        return self.recorded or []

    # ------------------------------------------------------------- cut-out images
    def images_dir(self) -> Path:
        return self.project_path.parent / f"{self.project_path.stem}_images"

    def add_template(self, label: str, x: int, y: int, w: int, h: int, threshold: float = 0.8):
        frame = self.grab_frame()
        if frame is None:
            raise RuntimeError("まだ画面が取り込まれていません。先に「見るだけ」を押してください")
        H, W = frame.shape[:2]
        x, y = max(0, int(x)), max(0, int(y))
        w, h = min(int(w), W - x), min(int(h), H - y)
        if w < 4 or h < 4:
            raise ValueError("切り取る範囲が小さすぎます")
        d = self.images_dir()
        d.mkdir(parents=True, exist_ok=True)
        fname = f"{safe_project_name(label)}.png"
        cv2.imwrite(str(d / fname), frame[y:y + h, x:x + w])
        rel = f"{d.name}/{fname}"
        settings = json.loads(json.dumps(self.project.get("settings", {})))
        det = settings.setdefault("detector", {})
        images = [im for im in det.get("images", []) if im.get("label") != label]
        images.append({"label": label, "image": rel, "threshold": threshold})
        det["images"] = images
        self.set_settings(settings)
        return settings

    def state(self) -> dict:
        return {"project": self.project, "mode": self.mode, "status": self.status,
                "recording": self.recorder is not None,
                "error": self.error, "graph_error": self.graph_error, "labels": self.labels,
                "project_file": str(self.project_path.name)}

    def set_graph(self, graph: dict, save=True):
        try:
            g = Graph(graph)
            self.graph_error = None
        except GraphError as e:
            self.graph_error = str(e)
            g = None
        with self.lock:
            self.project["graph"] = graph
            if g is not None:
                # keep per-node state (cooldowns...) for nodes that still exist
                if self.graph is not None:
                    for nid, st in self.graph.state.items():
                        if nid in g.state:
                            g.state[nid] = st
                self.graph = g
        if save:
            self.save()
        return self.graph_error

    def set_settings(self, settings: dict):
        with self.lock:
            self.project["settings"] = settings
            self.save()
        threading.Thread(target=self._load_settings, daemon=True).start()

    def set_mode(self, mode: str):
        if mode not in ("stop", "watch", "live"):
            raise ValueError(mode)
        with self.lock:
            self.mode = mode
            if mode != "live" and self._live_input:
                self._live_input.set_move(None)

    def _load_settings(self):
        s = self.project.get("settings", {})
        self.status, self.error = "loading", None
        try:
            src_spec = dict(s.get("source", {"type": "demo"}))
            if src_spec.get("type") == "demo":
                src_spec = {"type": "python", "factory": "bridge:make_sim",
                            "seed": src_spec.get("seed", 0),
                            "dt": 1.0 / float(s.get("fps", 20))}
            source = build_source(src_spec, ROOT / "demo")
            detectors = build_detectors(detector_specs(s.get("detector", {}), self.project_path.parent), ROOT)
            labels = [l for d in detectors for l in _labels_of(d)]
            sim_input = source.input_backend() if hasattr(source, "input_backend") else None
            live_input = None
            if sim_input is None:
                live_input = KeyboardMouseInput(source, (s.get("input") or {}).get("move_keys"))
            with self.lock:
                if self.source:
                    self.source.close()
                self.source, self.detectors, self.labels = source, detectors, labels
                self._sim_input, self._live_input = sim_input, live_input
                self.tracker.reset()
                self.motion = MotionTracker(min_speed=float(s.get("stuck_min_speed", 25)))
            self.status = "running"
        except Exception as e:
            traceback.print_exc()
            self.status, self.error = "error", friendly_error(e)

    def _start_stop_key(self):
        try:
            from pynput import keyboard

            def on_press(k):
                rec = self.recorder
                if rec is not None and k != keyboard.Key.f12:
                    rec.add_key(k)
                if k == keyboard.Key.f12 and self.recorder is not None:
                    self.stop_recording()
                elif k == keyboard.Key.f12 and self.mode == "live":
                    self.set_mode("stop")
            keyboard.Listener(on_press=on_press, daemon=True).start()
            self._has_key_listener = True
        except Exception as e:  # pragma: no cover
            print(f"F12 stop key unavailable: {e}")

    # ------------------------------------------------------------- loop
    def _loop(self):
        while True:
            fps = float(self.project.get("settings", {}).get("fps", 20))
            t0 = time.perf_counter()
            if self.mode == "stop" or self.status != "running":
                time.sleep(0.1)
                continue
            try:
                self._tick()
            except Exception as e:
                traceback.print_exc()
                self.error = f"{type(e).__name__}: {e}"
                self.mode = "stop"
            spare = 1.0 / fps - (time.perf_counter() - t0)
            if spare > 0:
                time.sleep(spare)

    def _tick(self):
        with self.lock:
            source, detectors, graph = self.source, self.detectors, self.graph
            anchor = self.project.get("settings", {}).get("anchor")
            mode = self.mode
            inp = self._sim_input or (self._live_input if mode == "live" else None) or NullInput()
            if self._sim_input and mode != "live":
                inp = NullInput()
        t0 = time.perf_counter()
        frame = source.grab()
        self.last_frame = frame
        now = source.time()
        t1 = time.perf_counter()
        dets = [d for det in detectors for d in det.detect(frame)]
        self.tracker.update(dets)
        t2 = time.perf_counter()
        groups, me = build_groups(dets, self.labels, anchor, frame.shape)
        self.motion.update(frame, me, now)
        ctx = Ctx(groups, me, now, frame.shape[1], frame.shape[0], self.motion.names())
        status = graph.tick(ctx) if graph else {}
        it = ctx.intents
        self.motion.after_decision(None if isinstance(inp, NullInput) else it.move)
        inp.set_move(it.move)
        for k in it.taps:
            inp.tap(k)
        for x, y in it.clicks:
            inp.click(x, y)
        for d in it.drags:
            inp.drag(*d)
        t3 = time.perf_counter()
        self._publish(frame, dets, me, status, it, mode,
                      {"capture": t1 - t0, "detect": t2 - t1, "logic": t3 - t2})

    def _publish(self, frame, dets, me, status, it, mode, timing):
        h, w = frame.shape[:2]
        scale = min(1.0, 1280 / w)
        img = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1 else frame
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 72])
        msg = {
            "type": "tick", "mode": mode, "w": w, "h": h,
            "image": base64.b64encode(jpg.tobytes()).decode() if ok else None,
            "dets": [to_dict(d) for d in dets],
            "me": to_dict(me) if me.exists else None,
            "nodes": status,
            "intents": {"move": it.move, "clicks": it.clicks, "drags": [d[:4] for d in it.drags],
                        "taps": it.taps},
            "motion": {"speed": round(self.motion.speed, 1), "stuck": round(self.motion.stuck_time, 2)},
            "timing": {k: round(v * 1000, 1) for k, v in timing.items()},
        }
        self._seq += 1
        self.packet = (self._seq, json.dumps(msg))


def create_app(project_path: Path):
    engine = Engine(project_path)
    app = FastAPI(title="SightFlow")
    app.state.engine = engine

    @app.get("/api/state")
    def get_state():
        return engine.state()

    @app.get("/api/node-types")
    def get_node_types():
        return node_types_json()

    @app.put("/api/graph")
    def put_graph(graph: dict = Body(...)):
        err = engine.set_graph(graph)
        return {"ok": err is None, "error": err}

    @app.put("/api/settings")
    def put_settings(settings: dict = Body(...)):
        engine.set_settings(settings)
        return {"ok": True}

    @app.post("/api/mode")
    def post_mode(body: dict = Body(...)):
        try:
            engine.set_mode(body.get("mode"))
        except ValueError:
            return JSONResponse({"ok": False}, status_code=400)
        return {"ok": True, "mode": engine.mode}

    @app.get("/api/projects")
    def get_projects():
        return {"projects": list_projects(engine.project_path, engine.known_paths)}

    @app.post("/api/projects/new")
    def post_new_project(body: dict = Body(...)):
        name = (body.get("name") or "").strip() or "新しいプロジェクト"
        PROJECTS.mkdir(exist_ok=True)
        stem, n = safe_project_name(name), 1
        path = PROJECTS / f"{stem}.json"
        while path.exists():
            n += 1
            path = PROJECTS / f"{stem}_{n}.json"
        path.write_text(json.dumps(new_project(name), ensure_ascii=False, indent=2), encoding="utf-8")
        engine.switch_project(path)
        return {"ok": True}

    @app.post("/api/projects/open")
    def post_open_project(body: dict = Body(...)):
        wanted = {p["file"] for p in list_projects(engine.project_path, engine.known_paths)}
        file = body.get("file")
        if file not in wanted:
            return JSONResponse({"ok": False, "error": "そのプロジェクトは見つかりません"}, status_code=404)
        engine.switch_project(Path(file))
        return {"ok": True}

    @app.put("/api/project/name")
    def put_project_name(body: dict = Body(...)):
        name = (body.get("name") or "").strip()
        if name:
            with engine.lock:
                engine.project["name"] = name
            engine.save()
        return {"ok": True}

    @app.post("/api/record/start")
    def post_record_start():
        try:
            engine.start_recording()
        except Exception as e:
            return JSONResponse({"ok": False, "error": f"記録を始められません: {e}"}, status_code=400)
        return {"ok": True}

    @app.post("/api/record/stop")
    def post_record_stop():
        return {"ok": True, "steps": engine.stop_recording()}

    @app.post("/api/templates")
    def post_template(body: dict = Body(...)):
        label = (body.get("label") or "").strip()
        if not label:
            return JSONResponse({"ok": False, "error": "名前を入れてください"}, status_code=400)
        try:
            settings = engine.add_template(label, body.get("x", 0), body.get("y", 0),
                                           body.get("w", 0), body.get("h", 0),
                                           float(body.get("threshold", 0.8)))
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        return {"ok": True, "settings": settings}

    @app.get("/api/templates/image")
    def get_template_image(label: str):
        for im in engine.project.get("settings", {}).get("detector", {}).get("images", []):
            if im.get("label") == label:
                path = (engine.project_path.parent / im["image"]).resolve()
                if path.is_file() and engine.project_path.parent.resolve() in path.parents:
                    return FileResponse(path)
        return JSONResponse({"ok": False}, status_code=404)

    @app.get("/api/windows")
    def get_windows():
        return {"windows": list_windows()}

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        last = 0
        try:
            while True:
                pkt = engine.packet
                if pkt and pkt[0] != last:
                    last = pkt[0]
                    await sock.send_text(pkt[1])
                else:
                    await sock.send_text(json.dumps({"type": "status", "mode": engine.mode,
                                                     "recording": engine.recorder is not None,
                                                     "status": engine.status, "error": engine.error}))
                    await asyncio.sleep(0.25)
                    continue
                await asyncio.sleep(1 / 30)
        except (WebSocketDisconnect, RuntimeError):
            pass

    if DIST.exists():  # the built web UI (webui/dist); must be mounted last
        app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")

    return app


def serve(project: str | None, host: str, port: int, open_browser: bool = True):
    import os

    import uvicorn

    (ROOT / "models").mkdir(exist_ok=True)
    os.chdir(ROOT / "models")   # Ultralytics downloads weights into the working dir

    path = Path(project).resolve() if project else DEFAULT_PROJECT
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(new_project(path.stem), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"created a new project: {path}")
    if not DIST.exists():
        print("note: web UI not built yet -> cd webui && npm install && npm run build")
    url = f"http://{host}:{port}"
    import socket

    with socket.socket() as probe:
        if probe.connect_ex((host, port)) == 0:   # already running (e.g. double-clicked twice)
            print(f"SightFlow is already running: {url}")
            if open_browser:
                import webbrowser
                webbrowser.open(url)
            return
    print(f"SightFlow: {url}   project: {path}")
    if open_browser:
        import webbrowser

        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(create_app(path), host=host, port=port, log_level="warning")
