"""Screen recognition: similarity methods, ROI, hysteresis and persistence."""
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sightflow.screens import (  # noqa: E402
    ScreenRegistry, ScreenState, score_frame,
)

W, H = 320, 240


def frame_with(x=50, y=50, color=(255, 255, 255), label="A"):
    img = np.full((H, W, 3), 20, np.uint8)
    cv2.rectangle(img, (x, y), (x + 100, y + 60), color, -1)
    cv2.putText(img, label, (x + 20, y + 45), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 0, 0), 2)
    return img


def test_template_scores_identical_frame_highest():
    a = frame_with()
    same = score_frame(a, a, method="template")
    other = score_frame(frame_with(150, 120, (0, 0, 255), "B"), a, method="template")
    assert same > 0.99
    assert other < 0.9
    assert same > other


def test_template_is_robust_to_brightness():
    a = frame_with()
    brighter = np.clip(a.astype(np.int16) + 20, 0, 255).astype(np.uint8)
    assert score_frame(brighter, a, method="template") > 0.9


def test_roi_ignores_the_rest_of_the_screen():
    a = frame_with(20, 20)
    roi = [0.0, 0.0, 0.45, 0.45]
    template = a[: int(H * 0.45), : int(W * 0.45)]       # what registration stores
    masked = a.copy()
    cv2.rectangle(masked, (220, 150), (300, 220), (0, 255, 0), -1)   # outside the ROI
    inside = score_frame(masked, template, roi=roi, method="template")
    assert inside > 0.99


def test_phash_and_ssim_rank_identical_above_different():
    a = frame_with()
    b = frame_with(150, 120, (0, 0, 255), "B")
    assert score_frame(a, a, method="phash") > 0.99
    assert score_frame(b, a, method="phash") < score_frame(a, a, method="phash")
    assert score_frame(a, a, method="ssim") > 0.99
    assert score_frame(b, a, method="ssim") < score_frame(a, a, method="ssim")


def test_registry_registers_and_persists(tmp_path):
    reg = ScreenRegistry(tmp_path)
    spec = reg.register(frame_with(), "title", threshold=0.9, enter_frames=2)
    assert (tmp_path / spec.template).exists()
    assert spec.public()["has_image"] is True

    reloaded = ScreenRegistry(tmp_path, reg.to_specs())
    assert len(reloaded.specs) == 1
    assert reloaded.images[spec.id] is not None
    assert reloaded.score_one(spec.id, frame_with()) > 0.9


def test_enter_and_exit_need_stable_frames(tmp_path):
    reg = ScreenRegistry(tmp_path)
    spec = reg.register(frame_with(), "A", threshold=0.9, enter_frames=2, exit_frames=2)
    a, b = frame_with(), frame_with(150, 120, (0, 0, 255), "B")

    assert reg.update(a).current is None           # streak 1
    assert reg.update(a).current == spec.id        # streak 2 -> stable
    assert reg.update(b).current == spec.id        # below 1, still A
    state = reg.update(b)                          # below 2 -> unknown
    assert state.current is None
    assert state.stable is False


def test_switches_to_a_better_screen_after_hysteresis(tmp_path):
    reg = ScreenRegistry(tmp_path)
    a = frame_with()
    b = frame_with(150, 120, (0, 0, 255), "B")
    sa = reg.register(a, "A", threshold=0.9, enter_frames=1, exit_frames=2)
    sb = reg.register(b, "B", threshold=0.9, enter_frames=1, exit_frames=2)

    assert reg.update(a).current == sa.id
    assert reg.update(b).current == sa.id          # A is still in its hysteresis band? no: below 1
    state = reg.update(b)
    assert state.current == sb.id
    assert state.name == "B"
    assert state.stable is True


def test_update_replace_and_remove(tmp_path):
    reg = ScreenRegistry(tmp_path)
    spec = reg.register(frame_with(), "old", threshold=0.5)
    reg.update_spec(spec.id, name="new", threshold=0.95, enter_frames=1)
    assert reg.spec(spec.id).name == "new"
    assert reg.spec(spec.id).threshold == 0.95

    new_frame = frame_with(150, 120, (0, 0, 255), "B")
    reg.replace_template(spec.id, new_frame)
    assert reg.score_one(spec.id, new_frame) > 0.95

    path = tmp_path / spec.template
    assert reg.remove(spec.id) is True
    assert not path.exists()
    assert reg.specs == []


def test_missing_template_is_kept_but_scores_zero(tmp_path):
    reg = ScreenRegistry(tmp_path, [{"id": "s_x", "name": "gone", "template": "screens/none.png"}])
    assert len(reg.specs) == 1
    assert reg.score_one("s_x", frame_with()) == 0.0


def test_multiple_templates_score_the_best_one(tmp_path):
    reg = ScreenRegistry(tmp_path)
    a = frame_with()
    b = frame_with(150, 120, (0, 0, 255), "B")
    spec = reg.register(a, "A", threshold=0.9)
    reg.add_template(spec.id, b)
    assert len(spec.templates) == 2
    assert (tmp_path / spec.templates[1]).exists()
    assert reg.score_one(spec.id, a) > 0.9
    assert reg.score_one(spec.id, b) > 0.9

    reloaded = ScreenRegistry(tmp_path, reg.to_specs())
    assert reloaded.spec(spec.id).public()["template_count"] == 2
    assert reloaded.score_one(spec.id, b) > 0.9

    reg.remove_template(spec.id, 1)
    assert len(spec.templates) == 1
    assert not (tmp_path / spec.templates[0]).name.startswith(spec.id + "_2")
    with pytest.raises(ValueError):
        reg.remove_template(spec.id, 0)


def test_public_state_has_candidates():
    state = ScreenState("s_a", "A", 0.93, True, {"s_a": 0.93},
                        [{"id": "s_a", "name": "A", "score": 0.93, "current": True}])
    d = state.to_dict()
    assert d["current"] == "s_a" and d["score"] == 0.93
    assert d["candidates"][0]["name"] == "A"
