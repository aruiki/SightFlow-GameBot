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
import numpy as np
from fastapi import Body, FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .bot import build_groups
from .dataset import DatasetStore, FrameRecorder, sanitize_name, train_yolo
from .detection import to_dict
from .detectors import build_detectors
from .graph import Ctx, Graph, GraphError, node_types_json
from .io import KeyboardMouseInput, NullInput, build_source, list_windows
from .motion import MotionTracker
from .screens import ScreenRegistry
from .tracking import CentroidTracker

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "webui" / "dist"
DEFAULT_PROJECT = ROOT / "demo" / "arena_project.json"
BACKGROUND_MSG = "game window is not in the foreground (click it to resume)"


def detector_specs(det: dict) -> list[dict]:
    kind = det.get("type", "yolo_world")
    common = {k: det[k] for k in ("conf", "imgsz", "device", "iou") if k in det}
    areas = list(det.get("region", []))
    if kind == "yolo_world":
        classes = {c["label"]: c.get("prompt") or c["label"] for c in det.get("classes", []) if c.get("label")}
        label_conf = {c["label"]: float(c["conf"]) for c in det.get("classes", [])
                      if c.get("label") and c.get("conf") is not None}
        return [{"type": "yolo_world", "model": det.get("model") or "yolov8s-worldv2.pt",
                 "classes": classes, "label_conf": label_conf, **common}] + areas
    if kind == "yolo":
        labels = {c["label"]: c.get("prompt") or c["label"] for c in det.get("classes", []) if c.get("label")}
        label_conf = {c["label"]: float(c["conf"]) for c in det.get("classes", [])
                      if c.get("label") and c.get("conf") is not None}
        return [{"type": "yolo", "model": det.get("model") or "yolo11s.pt",
                 "labels": labels or None, "label_conf": label_conf, **common}] + areas
    if kind == "color":
        return list(det.get("color", [])) + areas
    if kind == "region":
        return areas + list(det.get("color", []))
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
            "screens": [],
        },
        "graph": {"nodes": [], "edges": []},
    }


def _labels_of(d) -> list[str]:
    if hasattr(d, "labels"):
        return list(d.labels)
    if hasattr(d, "map"):
        return list(d.map.values())
    return [d.label]


class Engine:
    def __init__(self, project_path: Path):
        self.project_path = project_path
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
        self.screens = ScreenRegistry(project_path.parent,
                                      self.project.get("settings", {}).get("screens", []))
        self.screen_state = self.screens.state()
        self._train_lock = threading.Lock()
        self.train_state = {"status": "idle", "progress": 0.0, "message": "",
                            "model": "", "name": "", "started": None, "finished": None}
        self.recorder: FrameRecorder | None = None
        self.paused = False
        self._step_once = False
        self.set_graph(self.project.get("graph", {"nodes": [], "edges": []}), save=False)
        threading.Thread(target=self._load_settings, daemon=True).start()
        threading.Thread(target=self._loop, daemon=True).start()
        self._start_stop_key()

    # ------------------------------------------------------------- project
    def save(self):
        self.project_path.write_text(json.dumps(self.project, ensure_ascii=False, indent=2),
                                     encoding="utf-8")

    def state(self) -> dict:
        with self.lock:
            return {"project": self.project, "mode": self.mode, "status": self.status,
                    "error": self.error, "graph_error": self.graph_error, "labels": self.labels,
                    "screens": self.screens.to_specs(),
                    "screen": self.screen_state.to_dict(),
                    "paused": self.paused,
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
            if "screens" not in settings:      # keep the registered screens
                settings["screens"] = self.project.get("settings", {}).get("screens", [])
            self.project["settings"] = settings
            self.save()
        threading.Thread(target=self._load_settings, daemon=True).start()

    def set_mode(self, mode: str):
        if mode not in ("stop", "watch", "live"):
            raise ValueError(mode)
        with self.lock:
            self.mode = mode
            if mode == "stop":
                self.paused = False
                self._step_once = False
            if mode != "live":
                if self._live_input:
                    self._live_input.set_move(None)
                    getattr(self._live_input, "release_all", lambda: None)()
                if self.graph:
                    self.graph.release_holds()
            source = self.source
        if mode == "live" and source is not None and hasattr(source, "focus"):
            try:
                source.focus()
            except Exception:
                traceback.print_exc()

    def set_playback(self, action: str) -> dict:
        if action not in ("pause", "resume", "step"):
            raise ValueError(action)
        with self.lock:
            if action == "pause":
                self.paused = True
            elif action == "resume":
                self.paused, self._step_once = False, False
            else:
                self.paused, self._step_once = True, True
            return {"paused": self.paused, "mode": self.mode}

    def _load_settings(self):
        s = self.project.get("settings", {})
        with self.lock:
            self.screens = ScreenRegistry(self.project_path.parent, s.get("screens", []))
            self.screen_state = self.screens.state()
        self.status, self.error = "loading", None
        try:
            src_spec = dict(s.get("source", {"type": "demo"}))
            if src_spec.get("type") == "demo":
                src_spec = {"type": "python", "factory": "bridge:make_sim",
                            "seed": src_spec.get("seed", 0),
                            "dt": 1.0 / float(s.get("fps", 20))}
            source = build_source(src_spec, ROOT / "demo")
            detectors = build_detectors(detector_specs(s.get("detector", {})), ROOT)
            labels = [l for d in detectors for l in _labels_of(d)]
            sim_input = source.input_backend() if hasattr(source, "input_backend") else None
            live_input = None
            if sim_input is None:
                live_input = KeyboardMouseInput(source, (s.get("input") or {}).get("move_keys"))
            with self.lock:
                if self._live_input:
                    getattr(self._live_input, "release_all", lambda: None)()
                if self.source:
                    self.source.close()
                self.source, self.detectors, self.labels = source, detectors, labels
                self._sim_input, self._live_input = sim_input, live_input
                self.tracker.reset()
                self.motion = MotionTracker(min_speed=float(s.get("stuck_min_speed", 25)))
            self.status = "running"
        except Exception as e:
            traceback.print_exc()
            self.status, self.error = "error", f"{type(e).__name__}: {e}"

    def _start_stop_key(self):
        try:
            from pynput import keyboard

            def on_press(k):
                if k == keyboard.Key.f12 and self.mode == "live":
                    self.set_mode("stop")
            keyboard.Listener(on_press=on_press, daemon=True).start()
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
            if self.paused:
                if not self._step_once:
                    time.sleep(0.05)
                    continue
                self._step_once = False
            try:
                self._tick()
            except Exception as e:
                traceback.print_exc()
                self.error = f"{type(e).__name__}: {e}"
                self.mode = "stop"
                if self._live_input:
                    getattr(self._live_input, "release_all", lambda: None)()
                if self.graph:
                    self.graph.release_holds()
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
            if mode == "live" and inp is self._live_input and hasattr(source, "is_foreground"):
                if not source.is_foreground():      # never type into another app
                    inp.set_move(None)
                    inp = NullInput()
                    self.error = BACKGROUND_MSG
                elif self.error == BACKGROUND_MSG:
                    self.error = None
        t0 = time.perf_counter()
        frame = source.grab()
        now = source.time()
        t1 = time.perf_counter()
        with self.lock:
            screens = self.screens
            screen_state = screens.update(frame)
            self.last_frame = frame.copy()
            self.screen_state = screen_state
        dets = [d for det in detectors for d in det.detect(frame)]
        self.tracker.update(dets)
        t2 = time.perf_counter()
        groups, me = build_groups(dets, self.labels, anchor, frame.shape)
        self.motion.update(frame, me, now)
        ctx = Ctx(groups, me, now, frame.shape[1], frame.shape[0],
                  self.motion.names(), screens=screen_state)
        status = graph.tick(ctx) if graph else {}
        it = ctx.intents
        self.motion.after_decision(None if isinstance(inp, NullInput) else it.move)
        inp.set_move(it.move)
        for k in it.key_downs:
            inp.key_down(k)
        for k in it.key_ups:
            inp.key_up(k)
        for k in it.taps:
            inp.tap(k)
        for x, y in it.clicks:
            inp.click(x, y)
        for x, y, press in it.casts:
            inp.cast(x, y, press)
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
            "paused": self.paused,
            "image": base64.b64encode(jpg.tobytes()).decode() if ok else None,
            "dets": [to_dict(d) for d in dets],
            "me": to_dict(me) if me.exists else None,
            "nodes": status,
            "screen": self.screen_state.to_dict(),
            "screens": self.screen_state.candidates,
            "intents": {"move": it.move, "clicks": it.clicks, "casts": it.casts,
                        "drags": [d[:4] for d in it.drags],
                        "taps": it.taps, "keys": it.key_downs + it.key_ups},
            "motion": {"speed": round(self.motion.speed, 1), "stuck": round(self.motion.stuck_time, 2)},
            "timing": {k: round(v * 1000, 1) for k, v in timing.items()},
        }
        self._seq += 1
        self.packet = (self._seq, json.dumps(msg))


    # ------------------------------------------------------------- screens
    @staticmethod
    def _thumb(img, size: int = 160) -> str:
        if img is None or img.size == 0:
            return ""
        h, w = img.shape[:2]
        scale = min(1.0, size / max(w, h))
        if scale < 1:
            img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))),
                             interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return base64.b64encode(buf.tobytes()).decode() if ok else ""

    def list_screens(self) -> dict:
        with self.lock:
            specs = self.screens.to_specs()
            scores = dict(self.screens.scores)
            images = dict(self.screens.images)
            state = self.screen_state
        out = []
        for spec in specs:
            item = dict(spec)
            item["score"] = round(scores.get(spec["id"], 0.0), 3)
            item["current"] = spec["id"] == state.current
            imgs = images.get(spec["id"]) or []
            item["thumbnail"] = self._thumb(imgs[0] if imgs else None)
            out.append(item)
        return {"screens": out, "current": state.to_dict()}

    def _frame(self, body: dict | None = None):
        if body and body.get("image"):
            raw = str(body["image"])
            if raw.startswith("data:") and "," in raw:
                raw = raw.split(",", 1)[1]
            arr = np.frombuffer(base64.b64decode(raw), np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                raise ValueError("invalid image")
            return img
        with self.lock:
            frame = self.last_frame
            source = self.source
        if frame is None and source is not None:
            frame = source.grab()
        if frame is None:
            raise ValueError("no frame yet: switch to 見るだけ first")
        return frame

    def save_screens(self):
        with self.lock:
            self.project.setdefault("settings", {})["screens"] = self.screens.to_specs()
            self.save()

    def register_screen(self, body: dict) -> dict:
        frame = self._frame(body)
        with self.lock:
            spec = self.screens.register(
                frame, name=str(body.get("name") or "screen"),
                roi=body.get("roi"), method=str(body.get("method") or "template"),
                threshold=float(body.get("threshold", 0.85)),
                enter_frames=int(body.get("enter_frames", 2)),
                exit_frames=int(body.get("exit_frames", 2)),
                hysteresis=float(body.get("hysteresis", 0.08)))
        self.save_screens()
        return spec.public()

    def update_screen(self, sid: str, body: dict) -> dict:
        keys = ("name", "roi", "method", "threshold", "enter_frames", "exit_frames", "hysteresis")
        with self.lock:
            if self.screens.spec(sid) is None:
                raise ValueError(f"unknown screen {sid!r}")
            spec = self.screens.update_spec(sid, **{k: body[k] for k in keys if k in body})
        self.save_screens()
        return spec.public()

    def replace_screen_template(self, sid: str, body: dict) -> dict:
        frame = self._frame(body)
        with self.lock:
            if self.screens.spec(sid) is None:
                raise ValueError(f"unknown screen {sid!r}")
            spec = self.screens.replace_template(sid, frame, body.get("roi"),
                                                 int(body.get("index", 0)))
        self.save_screens()
        return spec.public()

    def add_screen_template(self, sid: str, body: dict) -> dict:
        frame = self._frame(body)
        with self.lock:
            if self.screens.spec(sid) is None:
                raise ValueError(f"unknown screen {sid!r}")
            spec = self.screens.add_template(sid, frame, body.get("roi"))
        self.save_screens()
        return spec.public()

    def remove_screen_template(self, sid: str, index: int) -> dict:
        with self.lock:
            spec = self.screens.remove_template(sid, index)
        self.save_screens()
        return spec.public()

    def delete_screen(self, sid: str) -> bool:
        with self.lock:
            ok = self.screens.remove(sid)
        if ok:
            self.save_screens()
        return ok

    def test_screen(self, sid: str, body: dict | None = None) -> dict:
        frame = self._frame(body)
        with self.lock:
            score = self.screens.score_one(sid, frame)
        return {"id": sid, "score": round(score, 3)}

    def capture_frame(self) -> dict:
        frame = self._frame()
        folder = self.project_path.parent / "screens"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"capture_{int(time.time() * 1000)}.png"
        path = folder / name
        cv2.imwrite(str(path), frame)
        return {"path": str(path), "name": name}

    def current_frame(self) -> dict:
        frame = self._frame()
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if not ok:
            raise ValueError("could not encode frame")
        return {"image": base64.b64encode(buf.tobytes()).decode(),
                "width": frame.shape[1], "height": frame.shape[0]}

    @staticmethod
    def list_models() -> list[dict]:
        folder = ROOT / "models"
        if not folder.exists():
            return []
        out = []
        for p in sorted(folder.iterdir()):
            if p.is_file() and p.suffix.lower() in (".pt", ".onnx", ".engine", ".pth"):
                out.append({"name": p.name, "size": p.stat().st_size,
                            "mtime": int(p.stat().st_mtime)})
        return out

    # ------------------------------------------------------------- dataset
    def class_labels(self) -> list[str]:
        det = self.project.get("settings", {}).get("detector", {})
        if str(det.get("type", "")) == "color":
            labels = [c.get("label") for c in det.get("color", []) if c.get("label")]
        else:
            labels = [c.get("label") for c in det.get("classes", []) if c.get("label")]
        return [str(x) for x in labels] or list(self.labels)

    def dataset_store(self, name: str | None = None) -> DatasetStore:
        return DatasetStore(self.project_path.parent, name or self.project_path.stem,
                            self.class_labels())

    def dataset_info(self, name: str | None = None, limit: int = 80) -> dict:
        store = self.dataset_store(name)
        frames = store.frames(limit)
        for f in frames:
            f["thumbnail"] = self._thumb(store.read(f["id"]), 120)
        return {**store.stats(), "frames": frames}

    def dataset_frame(self, name: str | None, frame_id: str) -> dict:
        store = self.dataset_store(name)
        img = store.read(frame_id)
        if img is None:
            raise ValueError("frame not found")
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise ValueError("could not encode frame")
        return {"name": store.name, "id": frame_id, "split": store.split_of(frame_id),
                "classes": store.classes, "boxes": store.load_boxes(frame_id),
                "image": base64.b64encode(buf.tobytes()).decode(),
                "width": img.shape[1], "height": img.shape[0]}

    def dataset_capture(self, name: str | None, body: dict) -> dict:
        frame = self._frame(body)
        store = self.dataset_store(name)
        item = store.capture(frame)
        return {**item, "stats": store.stats()}

    def dataset_save(self, name: str | None, frame_id: str, boxes: list[dict]) -> dict:
        store = self.dataset_store(name)
        n = store.save_boxes(frame_id, boxes)
        return {"saved": n, "stats": store.stats()}

    def dataset_delete(self, name: str | None, frame_id: str) -> dict:
        store = self.dataset_store(name)
        ok = store.delete(frame_id)
        return {"ok": ok, "stats": store.stats()}

    def record_status(self) -> dict:
        with self.lock:
            rec = self.recorder
        if rec is None:
            return {"running": False, "count": 0, "interval": 0, "error": None,
                    "name": self.project_path.stem, "stats": None}
        return rec.status()

    def start_record(self, body: dict) -> dict:
        with self.lock:
            old = self.recorder
        if old is not None and old.running:
            old.stop()
        store = self.dataset_store(body.get("dataset"))
        interval = float(body.get("interval", 2.0))
        interval = max(0.2, min(60.0, interval))

        def get_frame():
            with self.lock:
                if self.mode not in ("watch", "live") or self.last_frame is None:
                    return None
                return self.last_frame.copy()

        rec = FrameRecorder(store, get_frame, interval)
        rec.start()
        with self.lock:
            self.recorder = rec
        return rec.status()

    def stop_record(self) -> dict:
        with self.lock:
            rec = self.recorder
        if rec is not None:
            rec.stop()
            return rec.status()
        return self.record_status()

    def dataset_autolabel(self, name: str | None, frame_id: str | None = None) -> dict:
        store = self.dataset_store(name)
        with self.lock:
            detectors = list(self.detectors)
        if not detectors:
            raise ValueError("detectors not ready: switch to 見るだけ first")
        ids = [frame_id] if frame_id else [f["id"] for f in store.frames(10000)]
        total = used = 0
        for fid in ids:
            img = store.read(fid)
            if img is None:
                continue
            dets = [d for det in detectors for d in det.detect(img)]
            total += store.autolabel(fid, dets)
            used += 1
        return {"frames": used, "boxes": total, "stats": store.stats()}

    def train_status(self) -> dict:
        with self._train_lock:
            return dict(self.train_state)

    def start_train(self, body: dict) -> dict:
        with self._train_lock:
            if self.train_state.get("status") == "running":
                raise ValueError("training is already running")
        store = self.dataset_store(body.get("dataset"))
        if store.stats()["images"] == 0:
            raise ValueError("no images in the dataset: capture some frames first")
        epochs = int(body.get("epochs", 40))
        imgsz = int(body.get("imgsz", 960))
        batch = int(body.get("batch", 8))
        if not 1 <= epochs <= 300:
            raise ValueError("epochs must be 1..300")
        if not 320 <= imgsz <= 1920:
            raise ValueError("imgsz must be 320..1920")
        if not 1 <= batch <= 64:
            raise ValueError("batch must be 1..64")
        model = str(body.get("model") or "yolo11n.pt").strip()
        if not model:
            raise ValueError("model is empty")
        store.write_data_yaml()
        with self._train_lock:
            self.train_state = {"status": "running", "progress": 0.0,
                                "message": "starting", "model": "",
                                "name": store.name, "started": time.time(),
                                "finished": None}
        threading.Thread(target=self._train_worker,
                         args=(store, model, epochs, imgsz, batch), daemon=True).start()
        return self.train_status()

    def _train_worker(self, store: DatasetStore, model: str,
                      epochs: int, imgsz: int, batch: int):
        def progress(p, message):
            with self._train_lock:
                self.train_state.update(progress=round(float(p), 3), message=str(message))

        try:
            dest = train_yolo(store.data_yaml, store.name, model, epochs, imgsz, batch,
                              project_dir=self.project_path.parent,
                              models_dir=ROOT / "models", on_progress=progress)
            with self._train_lock:
                self.train_state.update(status="done", progress=1.0,
                                        model=dest.name,
                                        message=f"saved models/{dest.name}",
                                        finished=time.time())
        except Exception as e:
            traceback.print_exc()
            with self._train_lock:
                self.train_state.update(status="error",
                                        message=f"{type(e).__name__}: {e}",
                                        finished=time.time())


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

    @app.get("/api/screens")
    def get_screens():
        return engine.list_screens()

    @app.post("/api/screens")
    def post_screen(body: dict = Body(...)):
        try:
            return {"ok": True, "screen": engine.register_screen(body)}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.put("/api/screens/{sid}")
    def put_screen(sid: str, body: dict = Body(...)):
        try:
            return {"ok": True, "screen": engine.update_screen(sid, body)}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/screens/{sid}/capture")
    def capture_screen(sid: str, body: dict = Body(default={})):
        try:
            return {"ok": True, "screen": engine.replace_screen_template(sid, body)}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/screens/{sid}/templates")
    def add_screen_template(sid: str, body: dict = Body(default={})):
        try:
            return {"ok": True, "screen": engine.add_screen_template(sid, body)}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.delete("/api/screens/{sid}/templates/{index}")
    def remove_screen_template(sid: str, index: int):
        try:
            return {"ok": True, "screen": engine.remove_screen_template(sid, index)}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/screens/{sid}/test")
    def test_screen(sid: str, body: dict | None = Body(default=None)):
        try:
            return {"ok": True, **engine.test_screen(sid, body)}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.delete("/api/screens/{sid}")
    def delete_screen(sid: str):
        return {"ok": engine.delete_screen(sid)}

    @app.get("/api/models")
    def get_models():
        return {"models": Engine.list_models()}

    @app.get("/api/labels")
    def get_labels():
        return {"labels": engine.labels}

    @app.post("/api/capture")
    def post_capture():
        try:
            return {"ok": True, **engine.capture_frame()}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.get("/api/frame")
    def get_frame():
        try:
            return {"ok": True, **engine.current_frame()}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    # ------------------------------------------------------------- dataset
    @app.get("/api/dataset")
    def get_dataset(name: str | None = None, limit: int = 80):
        try:
            return engine.dataset_info(name, int(limit))
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/dataset/capture")
    def capture_dataset(body: dict = Body(default={})):
        try:
            return {"ok": True, **engine.dataset_capture(body.get("dataset"), body)}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.get("/api/dataset/frame")
    def get_dataset_frame(name: str | None = None, frame: str = ""):
        try:
            return {"ok": True, **engine.dataset_frame(name, frame)}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.put("/api/dataset/labels")
    def put_dataset_labels(body: dict = Body(...)):
        try:
            return {"ok": True, **engine.dataset_save(body.get("dataset"), body.get("frame", ""),
                                                      body.get("boxes") or [])}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.delete("/api/dataset/frame")
    def delete_dataset_frame(name: str | None = None, frame: str = ""):
        try:
            return {"ok": True, **engine.dataset_delete(name, frame)}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/dataset/autolabel")
    def autolabel_dataset(body: dict = Body(default={})):
        try:
            return {"ok": True, **engine.dataset_autolabel(body.get("dataset"), body.get("frame"))}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.get("/api/dataset/record")
    def get_record():
        return engine.record_status()

    @app.post("/api/dataset/record")
    def post_record(body: dict = Body(default={})):
        try:
            if body.get("action") == "stop":
                return {"ok": True, **engine.stop_record()}
            return {"ok": True, **engine.start_record(body)}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.get("/api/dataset/export")
    def export_dataset(name: str | None = None):
        try:
            store = engine.dataset_store(name)
            store.write_data_yaml()
            return {"ok": True, "path": str(store.data_yaml),
                    "yaml": store.data_yaml.read_text(encoding="utf-8"),
                    "stats": store.stats()}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.get("/api/train")
    def get_train():
        return engine.train_status()

    @app.post("/api/train")
    def post_train(body: dict = Body(...)):
        try:
            return {"ok": True, **engine.start_train(body)}
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    @app.post("/api/mode")
    def post_mode(body: dict = Body(...)):
        try:
            engine.set_mode(body.get("mode"))
        except ValueError:
            return JSONResponse({"ok": False}, status_code=400)
        return {"ok": True, "mode": engine.mode}

    @app.post("/api/playback")
    def post_playback(body: dict = Body(...)):
        try:
            return {"ok": True, **engine.set_playback(body.get("action"))}
        except ValueError:
            return JSONResponse({"ok": False}, status_code=400)

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
                                                     "status": engine.status, "error": engine.error}))
                    await asyncio.sleep(0.25)
                    continue
                await asyncio.sleep(1 / 30)
        except (WebSocketDisconnect, RuntimeError):
            pass

    if DIST.exists():  # the built web UI (webui/dist); must be mounted last
        app.mount("/", StaticFiles(directory=DIST, html=True), name="ui")

    return app


def serve(project: str | None, host: str, port: int, template: str | None = None):
    import os
    import shutil

    import uvicorn

    project_path = Path(project).resolve() if project else None
    (ROOT / "models").mkdir(exist_ok=True)
    os.chdir(ROOT / "models")   # Ultralytics downloads weights into the working dir

    if template and not project:
        name = Path(template).stem
        src = ROOT / "templates" / f"{name}.json"
        if not src.exists():
            raise SystemExit(f"template not found: {src}")
        path = ROOT / "projects" / f"{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            shutil.copyfile(src, path)
            print(f"created a new project from template: {path}")
    else:
        path = project_path if project_path is not None else DEFAULT_PROJECT
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(new_project(path.stem), ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"created a new project: {path}")
    if not DIST.exists():
        print("note: web UI not built yet -> cd webui && npm install && npm run build")
    print(f"SightFlow: http://{host}:{port}   project: {path}")
    uvicorn.run(create_app(path), host=host, port=port, log_level="warning")
