"""Benchmark: rule-flow bot vs random input vs doing nothing, on Bush Arena.

    python demo/bench.py --episodes 10 --seconds 120

Every agent sees the same seeds. The flow bot only sees pixels.
"""
from __future__ import annotations

import argparse
import os
import random
import statistics
import sys
import time
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]

from sightflow import Bot, load_config  # noqa: E402


def run_episode(agent: str, seed: int, seconds: float):
    cfg, base = load_config(HERE / "arena_sim.yaml")
    cfg["source"]["seed"] = seed
    bot = Bot(cfg, base)
    game = bot.source.game
    rng = random.Random(seed + 1000)
    t_perceive = 0.0
    steps = 0
    while game.t < seconds and not game.game_over:
        if agent == "flow":
            t0 = time.perf_counter()
            bot.step()
            t_perceive += time.perf_counter() - t0
            steps += 1
        else:
            bot.source.grab()
            if agent == "random":
                bot.input.set_move((rng.uniform(-1, 1), rng.uniform(-1, 1)))
                if rng.random() < 0.3:
                    bot.input.click(rng.uniform(0, 640), rng.uniform(0, 480))
    ms = 1000 * t_perceive / steps if steps else 0.0
    return game.score, game.alive_time, game.kills, game.coins_taken, ms


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=10)
    ap.add_argument("--seconds", type=float, default=120)
    a = ap.parse_args()
    print(f"{a.episodes} episodes x up to {a.seconds:.0f}s game time\n")
    print(f"{'agent':>7} | {'score':>6} | {'survived s':>10} | {'kills':>5} | {'coins':>5} | ms/tick")
    for agent in ("idle", "random", "flow"):
        rows = [run_episode(agent, s, a.seconds) for s in range(a.episodes)]
        sc, al, ki, co, ms = (statistics.mean(c) for c in zip(*rows))
        ms_txt = f"{ms:6.1f}" if agent == "flow" else "     -"
        print(f"{agent:>7} | {sc:6.1f} | {al:10.1f} | {ki:5.1f} | {co:5.1f} | {ms_txt}")


if __name__ == "__main__":
    main()
