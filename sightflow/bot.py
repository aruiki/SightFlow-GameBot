"""Config loading and the main capture -> detect -> flow -> input loop."""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import yaml

from .detection import Detection, LabelGroup
from .detectors import build_detectors
from .flow import Flow, TickResult
from .io import build_input, build_source
from .motion import MotionTracker


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path: str | Path) -> tuple[dict, Path]:
    """Loads YAML; `extends: other.yaml` deep-merges this file over another."""
    path = Path(path).resolve()
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if "extends" in cfg:
        base, _ = load_config(path.parent / cfg.pop("extends"))
        cfg = _merge(base, cfg)
    return cfg, path.parent


def declared_labels(specs: list[dict]) -> list[str]:
    labels = []
    for s in specs:
        if "label" in s:
            labels.append(s["label"])
        elif s.get("classes"):
            labels += list(s["classes"])
        else:
            labels += list(s.get("labels") or {})
    return labels


def build_groups(dets: list[Detection], labels: list[str], anchor: str | None,
                 frame_shape) -> tuple[dict[str, LabelGroup], Detection]:
    """Groups detections by label, picks the anchor ("me") and fills in
    dx/dy/distance relative to it. Without an anchor, the frame centre is used."""
    by_label: dict[str, list[Detection]] = {l: [] for l in labels}
    for d in dets:
        by_label.setdefault(d.label, []).append(d)
    cands = by_label.get(anchor, []) if anchor else []
    if cands:
        me = max(cands, key=lambda d: (d.score, d.area))
    else:  # no anchor seen: measure from the frame centre, me.exists = False
        h, w = frame_shape[:2]
        me = Detection(anchor or "me", w / 2, h / 2, 0, 0, 0.0, exists=False)
    for d in dets:
        d.dx, d.dy = d.x - me.x, d.y - me.y
        d.distance = float(np.hypot(d.dx, d.dy))
    groups = {l: LabelGroup(l, sorted(v, key=lambda d: d.distance)) for l, v in by_label.items()}
    return groups, me


class Bot:
    def __init__(self, cfg: dict, base_dir: Path, dry_run: bool = False):
        self.cfg = cfg
        self.base_dir = base_dir
        self.detectors = build_detectors(cfg.get("detectors", []), base_dir)
        self.labels = declared_labels(cfg.get("detectors", []))
        self.anchor = cfg.get("anchor")
        self.flow = Flow(cfg["flow"])
        self.source = build_source(cfg.get("source", {}), base_dir)
        in_spec = {"type": "none"} if dry_run else cfg.get("input", {})
        self.input = build_input(in_spec, self.source)
        run = cfg.get("run", {})
        self.fps = float(run.get("fps", 20))
        self.realtime = bool(run.get("realtime", True))
        st = run.get("stuck", {}) or {}
        self.motion = MotionTracker(min_speed=float(st.get("min_speed", 25)),
                                    camera=bool(st.get("camera_motion", True)))
        self.last_frame = None
        self.last_groups: dict[str, LabelGroup] = {}
        self.last_me = None
        self.last_result: TickResult | None = None

    @classmethod
    def from_file(cls, path, dry_run=False) -> "Bot":
        cfg, base = load_config(path)
        return cls(cfg, base, dry_run=dry_run)

    def perceive(self, frame):
        dets = [d for det in self.detectors for d in det.detect(frame)]
        return build_groups(dets, self.labels, self.anchor, frame.shape)

    def step(self) -> TickResult:
        frame = self.source.grab()
        now = self.source.time()
        groups, me = self.perceive(frame)
        self.motion.update(frame, me, now)
        extra = {"frame": frame_info(frame), **self.motion.names()}
        res = self.flow.tick(groups, me, now, extra=extra)
        it = res.intents
        self.motion.after_decision(it.move)
        self.input.set_move(it.move)
        for k in it.taps:
            self.input.tap(k)
        for k, secs in it.holds:
            self.input.hold(k, secs)
        for x, y in it.clicks:
            self.input.click(x, y)
        for fx, fy, tx, ty, secs in it.drags:
            self.input.drag(fx, fy, tx, ty, secs)
        for msg in it.logs:
            print(f"[{now:8.2f}] {res.state}: {msg}")
        self.last_frame, self.last_groups, self.last_me, self.last_result = frame, groups, me, res
        return res

    def run(self, seconds: float | None = None, overlay: bool = True,
            stop_key: str | None = "f12", on_step=None):
        """Main loop. Stops on `seconds`, the stop key, or 'q'/Esc in the overlay."""
        from . import overlay as ov

        stop = {"flag": False}
        listener = None
        if stop_key and self.realtime:
            try:
                from pynput import keyboard

                target = getattr(keyboard.Key, stop_key, None) or keyboard.KeyCode.from_char(stop_key)

                def on_press(k):
                    if k == target:
                        stop["flag"] = True

                listener = keyboard.Listener(on_press=on_press)
                listener.start()
            except Exception as e:  # headless / no pynput: just rely on other stops
                print(f"stop key disabled: {e}")
        if overlay:
            import cv2
        t0 = self.source.time()
        period = 1.0 / self.fps
        fps_est = None
        try:
            while not stop["flag"]:
                tick_start = time.perf_counter()
                res = self.step()
                if on_step and on_step(self, res) is False:
                    break
                if overlay:
                    img = ov.draw(self.last_frame, self.last_groups, self.last_me, res.state,
                                  res.rule, fps_est, res.intents.move, res.intents.clicks,
                                  res.intents.drags, self.motion)
                    cv2.imshow("sightflow debug", img)
                    if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                        break
                if seconds is not None and self.source.time() - t0 >= seconds:
                    break
                if self.realtime:
                    spare = period - (time.perf_counter() - tick_start)
                    if spare > 0:
                        time.sleep(spare)
                dt = time.perf_counter() - tick_start
                fps_est = 1 / dt if fps_est is None else 0.9 * fps_est + 0.1 / max(dt, 1e-6)
        finally:
            self.input.close()
            if listener:
                listener.stop()
            if overlay:
                cv2.destroyAllWindows()

    def snapshot(self) -> np.ndarray:
        from . import overlay as ov

        r = self.last_result
        return ov.draw(self.last_frame, self.last_groups, self.last_me, r.state, r.rule,
                       None, r.intents.move, r.intents.clicks, r.intents.drags, self.motion)

    def close(self):
        self.input.close()
        self.source.close()


class _FrameInfo:
    def __init__(self, frame):
        self.h, self.w = frame.shape[:2]


def frame_info(frame):
    return _FrameInfo(frame)
