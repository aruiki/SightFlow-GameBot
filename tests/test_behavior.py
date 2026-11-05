"""Tactical policy: target choice, gas avoidance and parallel channels.

These tests are the "algorithm is not too hard" proof: every requirement
(go to bush / avoid gas / attack enemy / do not oscillate) is checked against
the same deterministic tick.
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sightflow.behavior import (  # noqa: E402
    GasField, PolicyConfig, TacticalPolicy, score_bushes,
)
from sightflow.detection import Detection  # noqa: E402

W, H = 960.0, 540.0


def d(label, x, y, w=40, h=40, id=-1, score=1.0):
    return Detection(label, x, y, w, h, score=score, id=id)


def test_approaches_the_nearer_safe_bush():
    policy = TacticalPolicy()
    me = d("me", 100, 300, 10, 10)
    near, far = d("bush", 400, 300, 80, 80, id=1), d("bush", 800, 300, 80, 80, id=2)
    dec = policy.tick(0.0, me, [near, far], frame_w=W, frame_h=H)
    assert dec.mode == "approach"
    assert dec.target is near
    assert dec.move is not None and dec.move[0] > 0.5 and abs(dec.move[1]) < 0.3


def test_gas_covered_bush_is_not_chosen_even_if_closer():
    policy = TacticalPolicy()
    me = d("me", 500, 300, 10, 10)
    covered = d("bush", 200, 300, 80, 80, id=1)      # closer, but inside gas
    safe = d("bush", 800, 300, 80, 80, id=2)
    gas = GasField([d("gas", 200, 300, 120, 120)])
    dec = policy.tick(0.0, me, [covered, safe], gas=gas, frame_w=W, frame_h=H)
    assert dec.target is safe
    assert dec.move is not None and dec.move[0] > 0.5


def test_scores_rank_safety_before_distance():
    me = d("me", 50, 300, 10, 10)
    covered = d("bush", 200, 300, 80, 80, id=1)
    safe = d("bush", 800, 300, 80, 80, id=2)
    gas = GasField([d("gas", 200, 300, 120, 120)])
    ranks = {b.id: s for b, s in score_bushes([covered, safe], me, [], gas, W, H,
                                              PolicyConfig())}
    assert ranks[2] > ranks[1]


def test_attacks_while_moving_towards_a_bush():
    # The two channels are independent: movement + attack in the same tick.
    policy = TacticalPolicy()
    me = d("me", 100, 300, 10, 10)
    bush = d("bush", 700, 300, 80, 80, id=1)
    enemy = d("enemy", 380, 300, 20, 20, id=5)
    dec = policy.tick(0.0, me, [bush], [enemy], frame_w=W, frame_h=H)
    assert dec.attack is True and dec.attack_target is enemy
    assert dec.move is not None and dec.move[0] > 0.5
    assert dec.mode == "approach"


def test_keeps_hiding_in_a_safe_bush_instead_of_crossing_open_ground():
    policy = TacticalPolicy()
    me = d("me", 480, 270, 10, 10)
    standing = d("bush", 480, 270, 80, 80, id=1)
    bigger = d("bush", 760, 270, 300, 300, id=2)
    assert policy.tick(0.0, me, [standing], frame_w=W, frame_h=H).mode == "hide"
    dec = policy.tick(0.1, me, [standing, bigger], frame_w=W, frame_h=H)
    assert dec.mode == "hide"
    assert dec.move is None
    assert dec.target is standing


def test_leaves_a_bush_that_the_gas_reaches():
    policy = TacticalPolicy()
    me = d("me", 480, 270, 10, 10)
    standing = d("bush", 480, 270, 80, 80, id=1)
    gas = GasField([d("gas", 480, 270, 300, 300)])
    dec = policy.tick(0.0, me, [standing], gas=gas, frame_w=W, frame_h=H)
    assert dec.mode == "rotate"
    assert dec.move is not None



    policy = TacticalPolicy()
    me = d("me", 100, 300, 10, 10)
    bush = d("bush", 700, 300, 80, 80, id=1)
    enemy = d("enemy", 380, 300, 20, 20, id=5)
    assert policy.tick(0.0, me, [bush], [enemy], frame_w=W, frame_h=H).attack
    assert not policy.tick(0.1, me, [bush], [enemy], frame_w=W, frame_h=H).attack
    assert policy.tick(0.4, me, [bush], [enemy], frame_w=W, frame_h=H).attack


def test_attack_uses_the_nearest_enemy():
    policy = TacticalPolicy()
    me = d("me", 300, 300, 10, 10)
    far = d("enemy", 550, 300, 20, 20, id=1)
    near = d("enemy", 460, 300, 20, 20, id=2)
    bush = d("bush", 800, 300, 80, 80, id=1)
    dec = policy.tick(0.0, me, [bush], [far, near], frame_w=W, frame_h=H)
    assert dec.attack_target is near


def test_hides_when_already_in_a_bush():
    policy = TacticalPolicy()
    me = d("me", 300, 300, 10, 10)
    bush = d("bush", 300, 300, 120, 120, id=1)
    dec = policy.tick(0.0, me, [bush], frame_w=W, frame_h=H)
    assert dec.mode == "hide"
    assert dec.move is None
    assert dec.attack is False


def test_hides_and_attacks_at_the_same_time():
    policy = TacticalPolicy()
    me = d("me", 300, 300, 10, 10)
    bush = d("bush", 300, 300, 120, 120, id=1)
    enemy = d("enemy", 460, 300, 20, 20, id=2)   # 160 px: in range
    dec = policy.tick(0.0, me, [bush], [enemy], frame_w=W, frame_h=H)
    assert dec.mode == "hide" and dec.move is None
    assert dec.attack is True and dec.attack_target is enemy


def test_gas_danger_preempts_hiding():
    policy = TacticalPolicy()
    me = d("me", 300, 300, 10, 10)
    here = d("bush", 300, 300, 120, 120, id=1)      # we are in it, but gas covers us
    safe = d("bush", 750, 300, 80, 80, id=2)
    gas = GasField([d("gas", 300, 300, 160, 160)])
    dec = policy.tick(0.0, me, [here, safe], gas=gas, frame_w=W, frame_h=H)
    assert dec.mode == "rotate"
    assert dec.move is not None and dec.move[0] > 0.2


def test_explores_away_from_gas_when_no_bush_is_visible():
    policy = TacticalPolicy()
    me = d("me", 500, 250, 10, 10)
    gas = GasField([d("gas", 500, 450, 300, 120)])   # below us
    dec = policy.tick(0.0, me, [], gas=gas, frame_w=W, frame_h=H)
    assert dec.target is None
    assert dec.move is not None and dec.move[1] < -0.5


def test_explores_up_with_no_bush_and_no_gas():
    policy = TacticalPolicy()
    me = d("me", 480, 400, 10, 10)
    dec = policy.tick(0.0, me, [], frame_w=W, frame_h=H)
    assert dec.mode == "explore"
    assert dec.move is not None and dec.move[1] < -0.5


def test_stuck_turns_sideways_instead_of_pushing_into_the_wall():
    policy = TacticalPolicy(PolicyConfig(stuck_time=0.4, stuck_hold=0.4))
    me = d("me", 300, 300, 10, 10)
    bush = d("bush", 850, 300, 60, 60, id=1)
    dec = None
    for t in (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6):
        dec = policy.tick(t, me, [bush], frame_w=W, frame_h=H)
    assert dec.mode == "stuck"
    assert dec.move is not None and abs(dec.move[1]) > 0.3


def test_target_hysteresis_keeps_the_current_bush_until_dwell_passes():
    cfg = PolicyConfig(switch_margin=0.3, min_dwell=2.0)
    policy = TacticalPolicy(cfg)
    me = d("me", 500, 300, 10, 10)
    a = d("bush", 520, 300, 40, 40, id=1)
    b = d("bush", 480, 300, 40, 40, id=2)
    assert policy.tick(0.0, me, [a, b], frame_w=W, frame_h=H).target.id == 1
    # b becomes much bigger, but the switch margin / dwell keeps us on a
    a2 = d("bush", 520, 300, 30, 30, id=1)
    b2 = d("bush", 500, 300, 140, 140, id=2)
    assert policy.tick(0.1, me, [a2, b2], frame_w=W, frame_h=H).target.id == 1
    # after the dwell time, the clearly better bush takes over
    assert policy.tick(3.0, me, [a2, b2], frame_w=W, frame_h=H).target.id == 2


def test_gas_mask_gives_a_clearance_field():
    import numpy as np

    mask = np.zeros((int(H), int(W)), bool)
    mask[:, : int(W) // 2] = True                    # left half is gas
    gas = GasField(mask=mask)
    assert gas.covers(100, 100) is True
    assert gas.covers(800, 100) is False
    assert gas.clearance(100, 100) == 0
    assert gas.clearance(800, 100) > 300


def test_decision_exposes_scores_for_the_ui():
    policy = TacticalPolicy()
    me = d("me", 100, 300, 10, 10)
    bushes = [d("bush", 400, 300, 80, 80, id=1), d("bush", 700, 200, 80, 80, id=2)]
    dec = policy.tick(0.0, me, bushes, frame_w=W, frame_h=H)
    assert len(dec.scores) == 2
    assert dec.target_score == max(s for _, s in dec.scores)
    assert math.isinf(dec.clearance)      # no gas -> UI shows "∞"
