"""Command line:

  python -m sightflow run CONFIG [--dry-run] [--no-overlay] [--seconds N] [--snapshot out.png]
  python -m sightflow detect CONFIG IMAGE [-o out.png]     test detectors on a screenshot
  python -m sightflow grab CONFIG -o shot.png               save one frame from the source
  python -m sightflow pick IMAGE                            click pixels to print HSV values
  python -m sightflow web [project.json]                    node editor + live view (browser)
"""
from __future__ import annotations

import argparse
import sys

import cv2

from . import overlay
from .bot import Bot, build_groups, declared_labels, load_config
from .detectors import build_detectors


def cmd_run(a):
    bot = Bot.from_file(a.config, dry_run=a.dry_run)
    print(f"running {a.config}  (stop: F12, or q/Esc in the overlay window)")
    try:
        bot.run(seconds=a.seconds, overlay=not a.no_overlay)
        if a.snapshot and bot.last_frame is not None:
            cv2.imwrite(a.snapshot, bot.snapshot())
            print(f"snapshot -> {a.snapshot}")
    finally:
        bot.close()


def cmd_detect(a):
    cfg, base = load_config(a.config)
    img = cv2.imread(a.image)
    if img is None:
        sys.exit(f"cannot read {a.image}")
    specs = cfg.get("detectors", [])
    dets = [d for det in build_detectors(specs, base) for d in det.detect(img)]
    groups, me = build_groups(dets, declared_labels(specs), cfg.get("anchor"), img.shape)
    for label, g in groups.items():
        print(f"{label:>10}: {g.count}")
        for d in g.items[:10]:
            print(f"            x={d.x:6.1f} y={d.y:6.1f} w={d.w:4.0f} h={d.h:4.0f} "
                  f"score={d.score:.2f} dist={d.distance:6.1f}")
    if a.out:
        cv2.imwrite(a.out, overlay.draw(img, groups, me, "-", None))
        print(f"overlay -> {a.out}")


def cmd_grab(a):
    cfg, base = load_config(a.config)
    from .io import build_source

    src = build_source(cfg.get("source", {}), base)
    cv2.imwrite(a.out, src.grab())
    src.close()
    print(f"frame -> {a.out}")


def cmd_pick(a):
    img = cv2.imread(a.image)
    if img is None:
        sys.exit(f"cannot read {a.image}")
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    def on_mouse(ev, x, y, *_):
        if ev == cv2.EVENT_LBUTTONDOWN:
            print(f"({x},{y}) BGR={img[y, x].tolist()} HSV={hsv[y, x].tolist()}")

    cv2.namedWindow("pick")
    cv2.setMouseCallback("pick", on_mouse)
    print("click pixels to print HSV; any key to quit")
    cv2.imshow("pick", img)
    cv2.waitKey(0)
    cv2.destroyAllWindows()


def cmd_nodes_doc(a):
    from .graph import nodes_markdown

    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdout.write(nodes_markdown())


def cmd_web(a):
    from .server import serve

    serve(a.project, a.host, a.port, open_browser=not a.no_browser)


def main(argv=None):
    p = argparse.ArgumentParser(prog="sightflow")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("config")
    r.add_argument("--dry-run", action="store_true", help="decide but send no input")
    r.add_argument("--no-overlay", action="store_true")
    r.add_argument("--seconds", type=float)
    r.add_argument("--snapshot")
    r.set_defaults(fn=cmd_run)
    d = sub.add_parser("detect")
    d.add_argument("config")
    d.add_argument("image")
    d.add_argument("-o", "--out")
    d.set_defaults(fn=cmd_detect)
    g = sub.add_parser("grab")
    g.add_argument("config")
    g.add_argument("-o", "--out", required=True)
    g.set_defaults(fn=cmd_grab)
    k = sub.add_parser("pick")
    k.add_argument("image")
    k.set_defaults(fn=cmd_pick)
    w = sub.add_parser("web", help="node editor + live view in the browser")
    w.add_argument("project", nargs="?")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8765)
    w.add_argument("--no-browser", action="store_true", help="do not open the browser automatically")
    w.set_defaults(fn=cmd_web)
    nd = sub.add_parser("nodes-doc", help="print the node reference (markdown)")
    nd.set_defaults(fn=cmd_nodes_doc)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
