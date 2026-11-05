"""Screen recognition for macros: template / pHash / SSIM + hysteresis.

A screen is a named reference image plus a region of interest.  Every tick the
engine scores the current frame against all registered screens and keeps a
stable `current` screen, so a single flicker cannot trigger a transition.

Template images live next to the project file (``<project>/screens/<id>.png``),
so a project stays portable by copying its folder.
"""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import cv2
import numpy as np

METHODS = ("template", "phash", "ssim")


# --------------------------------------------------------------------- images
def _gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def _crop(img: np.ndarray, roi: Sequence[float] | None) -> np.ndarray:
    """ROI is normalised (0..1). Invalid/empty ROI falls back to the whole frame."""
    if not roi or len(roi) != 4:
        return img
    h, w = img.shape[:2]
    x, y, rw, rh = (float(v) for v in roi)
    if max(abs(x), abs(y), abs(rw), abs(rh)) > 1.5:      # tolerate pixel ROIs
        rw, rh = rw / w, rh / h
        x, y = x / w, y / h
    x0, y0 = max(0, int(x * w)), max(0, int(y * h))
    x1, y1 = min(w, int((x + rw) * w)), min(h, int((y + rh) * h))
    return img[y0:y1, x0:x1] if x1 > x0 and y1 > y0 else img


def _phash_bits(img: np.ndarray) -> np.ndarray:
    g = cv2.resize(_gray(img), (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    d = cv2.dct(g)[:8, :8].flatten()
    vals = d[1:]                       # drop DC
    return vals > np.median(vals)


def _phash_similarity(a: np.ndarray, b: np.ndarray) -> float:
    ba, bb = _phash_bits(a), _phash_bits(b)
    return 1.0 - float(np.count_nonzero(ba ^ bb)) / max(1, ba.size)


def _ssim(a: np.ndarray, b: np.ndarray) -> float:
    x = cv2.resize(_gray(a), (64, 64), interpolation=cv2.INTER_AREA).astype(np.float64)
    y = cv2.resize(_gray(b), (64, 64), interpolation=cv2.INTER_AREA).astype(np.float64)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    mx, my = x.mean(), y.mean()
    vx, vy = x.var(), y.var()
    cov = ((x - mx) * (y - my)).mean()
    s = ((2 * mx * my + c1) * (2 * cov + c2)) / ((mx ** 2 + my ** 2 + c1) * (vx + vy + c2))
    return float(max(0.0, min(1.0, s)))


def score_frame(frame: np.ndarray, template: np.ndarray,
                roi: Sequence[float] | None = None, method: str = "template") -> float:
    """Similarity 0..1 between the current frame (ROI) and a stored template."""
    if frame is None or template is None or frame.size == 0 or template.size == 0:
        return 0.0
    cur = _crop(frame, roi)
    method = method if method in METHODS else "template"
    if method == "phash":
        return max(0.0, min(1.0, _phash_similarity(cur, template)))
    if method == "ssim":
        return _ssim(cur, template)

    g_cur = _gray(cur)
    g_tpl = _gray(template)
    if g_cur.shape != g_tpl.shape:      # window size changed -> normalise to template
        g_cur = cv2.resize(g_cur, (g_tpl.shape[1], g_tpl.shape[0]), interpolation=cv2.INTER_AREA)
    if g_tpl.size == 0 or g_cur.size == 0:
        return 0.0
    try:
        res = cv2.matchTemplate(g_cur, g_tpl, cv2.TM_CCOEFF_NORMED)
        v = float(res[0, 0]) if res.size else 0.0
        if not math.isfinite(v):
            v = 0.0
        return max(0.0, min(1.0, (v + 1.0) / 2.0))
    except cv2.error:
        diff = float(np.mean(cv2.absdiff(g_cur, g_tpl))) / 255.0
        return max(0.0, 1.0 - diff)


# ----------------------------------------------------------------------- spec
@dataclass
class ScreenSpec:
    id: str
    name: str
    template: str = ""
    templates: list[str] = field(default_factory=list)
    roi: list[float] | None = None
    method: str = "template"
    threshold: float = 0.85
    enter_frames: int = 2
    exit_frames: int = 2
    hysteresis: float = 0.08
    image: np.ndarray | None = field(default=None, repr=False, compare=False)

    def public(self) -> dict:
        return {"id": self.id, "name": self.name, "template": self.template,
                "templates": list(self.templates), "template_count": len(self.templates),
                "roi": self.roi, "method": self.method, "threshold": self.threshold,
                "enter_frames": self.enter_frames, "exit_frames": self.exit_frames,
                "hysteresis": self.hysteresis, "has_image": self.image is not None}


@dataclass
class ScreenState:
    current: str | None = None
    name: str | None = None
    score: float = 0.0
    stable: bool = False
    scores: dict[str, float] = field(default_factory=dict)
    candidates: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"current": self.current, "name": self.name, "score": round(self.score, 3),
                "stable": self.stable,
                "scores": {k: round(v, 3) for k, v in self.scores.items()},
                "candidates": self.candidates}


class ScreenRegistry:
    """All screens of one project + the stable current-screen state."""

    def __init__(self, base_dir: str | Path, specs: Iterable[dict] = ()):
        self.base_dir = Path(base_dir)
        self.specs: list[ScreenSpec] = []
        self.images: dict[str, list[np.ndarray]] = {}
        self.current: str | None = None
        self.score: float = 0.0
        self.stable: bool = False
        self.scores: dict[str, float] = {}
        self.candidates: list[dict] = []
        self._streak: dict[str, int] = {}
        self._below: int = 0
        self.set_specs(specs)

    # ----------------------------------------------------------------- setup
    def set_specs(self, specs: Iterable[dict]):
        self.specs = []
        self.images = {}
        for raw in specs or []:
            if not raw or not raw.get("id"):
                continue
            paths = raw.get("templates")
            if not paths:
                paths = [raw.get("template")] if raw.get("template") else []
            paths = [str(p) for p in paths if p]
            spec = ScreenSpec(
                id=str(raw["id"]), name=str(raw.get("name") or raw["id"]),
                template=paths[0] if paths else "", templates=paths, roi=raw.get("roi"),
                method=str(raw.get("method") or "template"),
                threshold=float(raw.get("threshold", 0.85)),
                enter_frames=max(1, int(raw.get("enter_frames", 2))),
                exit_frames=max(1, int(raw.get("exit_frames", 2))),
                hysteresis=float(raw.get("hysteresis", 0.08)),
            )
            imgs = [img for img in (self._load(p) for p in paths) if img is not None]
            spec.image = imgs[0] if imgs else None
            self.specs.append(spec)
            self.images[spec.id] = imgs
        self._reset_state()

    def _load(self, rel: str) -> np.ndarray | None:
        if not rel:
            return None
        p = Path(rel)
        if not p.is_absolute():
            p = self.base_dir / p
        if not p.exists():
            return None
        return cv2.imread(str(p))

    def _reset_state(self):
        self.current, self.score, self.stable = None, 0.0, False
        self.scores, self.candidates = {}, []
        self._streak, self._below = {}, 0

    @property
    def empty(self) -> bool:
        return not self.specs

    def spec(self, sid: str) -> ScreenSpec | None:
        return next((s for s in self.specs if s.id == sid), None)

    def state(self) -> ScreenState:
        return ScreenState(self.current, self.name_of(self.current), self.score,
                           self.stable, dict(self.scores), list(self.candidates))

    def name_of(self, sid: str | None) -> str | None:
        s = self.spec(sid) if sid else None
        return s.name if s else None

    # ---------------------------------------------------------------- scoring
    def score_one(self, sid: str, frame: np.ndarray) -> float:
        s = self.spec(sid)
        imgs = self.images.get(sid) or []
        if s is None or not imgs:
            return 0.0
        return max(score_frame(frame, img, s.roi, s.method) for img in imgs)

    def score_all(self, frame: np.ndarray) -> dict[str, float]:
        return {s.id: self.score_one(s.id, frame) for s in self.specs}

    def update(self, frame: np.ndarray) -> ScreenState:
        """Score the frame and advance the hysteresis state machine."""
        scores = self.score_all(frame)
        self.scores = scores
        best_id = max(scores, key=scores.get) if scores else None
        best = scores.get(best_id, 0.0) if best_id else 0.0

        for s in self.specs:
            ok = scores.get(s.id, 0.0) >= s.threshold
            self._streak[s.id] = self._streak.get(s.id, 0) + 1 if ok else 0

        if self.current is None:
            if (best_id and best >= self.spec(best_id).threshold
                    and self._streak.get(best_id, 0) >= self.spec(best_id).enter_frames):
                self.current, self._below = best_id, 0
        else:
            cur = self.spec(self.current)
            cur_s = scores.get(self.current, 0.0)
            if cur is None:
                self.current = None
            elif cur_s >= cur.threshold - cur.hysteresis:
                self._below = 0          # keep current (hysteresis band)
            else:
                self._below += 1
                nb = self.spec(best_id) if best_id else None
                if (nb is not None and best_id != self.current and best >= nb.threshold
                        and self._streak.get(best_id, 0) >= nb.enter_frames
                        and self._below >= cur.exit_frames):
                    self.current, self._below = best_id, 0
                elif self._below >= cur.exit_frames:
                    self.current, self._below = None, 0

        self.score = scores.get(self.current, 0.0) if self.current else best
        self.stable = bool(self.current) and (
            self.score >= self.spec(self.current).threshold
            or self._streak.get(self.current, 0) >= self.spec(self.current).enter_frames)
        self.candidates = [
            {"id": s.id, "name": s.name, "score": round(scores.get(s.id, 0.0), 3),
             "current": s.id == self.current}
            for s in sorted(self.specs, key=lambda s: scores.get(s.id, 0.0), reverse=True)
        ]
        return self.state()

    # ------------------------------------------------------------------ CRUD
    def register(self, frame: np.ndarray, name: str, roi: Sequence[float] | None = None,
                 method: str = "template", threshold: float = 0.85,
                 enter_frames: int = 2, exit_frames: int = 2,
                 hysteresis: float = 0.08) -> ScreenSpec:
        if frame is None or frame.size == 0:
            raise ValueError("no frame to register")
        name = (name or "").strip() or "screen"
        sid = "s_" + uuid.uuid4().hex[:8]
        crop = _crop(frame, roi).copy()
        folder = self.base_dir / "screens"
        folder.mkdir(parents=True, exist_ok=True)
        rel = Path("screens") / f"{sid}.png"
        if not cv2.imwrite(str(self.base_dir / rel), crop):
            raise OSError(f"could not write {self.base_dir / rel}")
        spec = ScreenSpec(
            id=sid, name=name, template=str(rel).replace("\\", "/"),
            templates=[str(rel).replace("\\", "/")],
            roi=list(roi) if roi else None,
            method=method if method in METHODS else "template",
            threshold=float(max(0.05, min(0.99, threshold))),
            enter_frames=max(1, int(enter_frames)),
            exit_frames=max(1, int(exit_frames)),
            hysteresis=float(max(0.0, min(0.5, hysteresis))),
            image=crop,
        )
        self.specs.append(spec)
        self.images[sid] = [crop]
        self._streak[sid] = 0
        return spec

    def replace_template(self, sid: str, frame: np.ndarray,
                         roi: Sequence[float] | None = None,
                         index: int = 0) -> ScreenSpec:
        spec = self.spec(sid)
        if spec is None:
            raise KeyError(sid)
        if frame is None or frame.size == 0:
            raise ValueError("no frame")
        if not 0 <= index < len(spec.templates):
            raise IndexError(index)
        crop = _crop(frame, roi if roi is not None else spec.roi).copy()
        p = self.base_dir / spec.templates[index]
        p.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(p), crop):
            raise OSError(f"could not write {p}")
        imgs = self.images.setdefault(sid, [])
        while len(imgs) <= index:
            imgs.append(None)
        imgs[index] = crop
        spec.image = imgs[0]
        self._streak[sid] = 0
        return spec

    def add_template(self, sid: str, frame: np.ndarray,
                     roi: Sequence[float] | None = None) -> ScreenSpec:
        spec = self.spec(sid)
        if spec is None:
            raise KeyError(sid)
        if frame is None or frame.size == 0:
            raise ValueError("no frame")
        crop = _crop(frame, roi if roi is not None else spec.roi).copy()
        folder = self.base_dir / "screens"
        folder.mkdir(parents=True, exist_ok=True)
        rel = Path("screens") / f"{sid}_{len(spec.templates) + 1}.png"
        if not cv2.imwrite(str(self.base_dir / rel), crop):
            raise OSError(f"could not write {self.base_dir / rel}")
        spec.templates.append(str(rel).replace("\\", "/"))
        if not spec.template:
            spec.template = str(rel).replace("\\", "/")
        self.images.setdefault(sid, []).append(crop)
        spec.image = self.images[sid][0]
        self._streak[sid] = 0
        return spec

    def remove_template(self, sid: str, index: int) -> ScreenSpec:
        spec = self.spec(sid)
        if spec is None:
            raise KeyError(sid)
        if len(spec.templates) <= 1:
            raise ValueError("a screen needs at least one template")
        if not 0 <= index < len(spec.templates):
            raise IndexError(index)
        p = self.base_dir / spec.templates[index]
        try:
            if p.exists() and p.resolve().is_relative_to(self.base_dir.resolve()):
                p.unlink()
        except OSError:
            pass
        spec.templates.pop(index)
        imgs = self.images.get(sid, [])
        if 0 <= index < len(imgs):
            imgs.pop(index)
        spec.template = spec.templates[0]
        spec.image = imgs[0] if imgs else None
        self._streak[sid] = 0
        return spec

    def update_spec(self, sid: str, **fields) -> ScreenSpec:
        spec = self.spec(sid)
        if spec is None:
            raise KeyError(sid)
        for key in ("name", "roi", "method", "threshold", "enter_frames",
                    "exit_frames", "hysteresis"):
            if key not in fields or fields[key] is None:
                continue
            v = fields[key]
            if key == "method":
                v = v if v in METHODS else spec.method
            elif key == "threshold":
                v = float(max(0.05, min(0.99, float(v))))
            elif key in ("enter_frames", "exit_frames"):
                v = max(1, int(v))
            elif key == "hysteresis":
                v = float(max(0.0, min(0.5, float(v))))
            setattr(spec, key, v)
        self._streak[sid] = 0
        return spec

    def remove(self, sid: str) -> bool:
        spec = self.spec(sid)
        if spec is None:
            return False
        for rel in list(spec.templates) or ([spec.template] if spec.template else []):
            p = self.base_dir / rel
            try:
                if p.exists() and p.resolve().is_relative_to(self.base_dir.resolve()):
                    p.unlink()
            except OSError:
                pass
        self.specs = [s for s in self.specs if s.id != sid]
        self.images.pop(sid, None)
        self._streak.pop(sid, None)
        if self.current == sid:
            self.current, self.score, self.stable = None, 0.0, False
        return True

    def to_specs(self) -> list[dict]:
        return [s.public() for s in self.specs]
