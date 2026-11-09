# SightFlow

**Build game bots without code: real object detection + nodes you wire together.**

SightFlow does not train an AI to play. A real object detector (YOLO-World / YOLO,
colour ranges, or images you cut out of the screen) says *what is visible*, and you
decide *what to do* by wiring nodes in the browser, ComfyUI-style. No training data,
no reward design, and every node shows its live value while the bot runs, so you can
see exactly where something goes wrong.

```
screen capture ─▶ detection + tracking IDs ─▶ node flow ─▶ keyboard / mouse
                                  ▲                          │
                                  └──── live view in browser ◀┘
```

> The UI text is Japanese for now. The guide ([docs/guide.md](docs/guide.md)) and node
> reference ([docs/nodes.md](docs/nodes.md)) are in Japanese as well.

## Quick start (Windows)

1. Download `SightFlow-…-windows.zip` from [Releases](https://github.com/aruiki/SightFlow-GameBot/releases) and unzip it
2. Install [Python 3.10+](https://www.python.org/downloads/) (tick "Add python.exe to PATH")
3. **Double-click `SightFlow.bat`.** The first run sets things up (a few minutes, installs GPU PyTorch when an NVIDIA GPU is found) and opens the browser

The welcome screen walks you through it: **pick the game window → Watch → record / cut out / add nodes → Operate (F12 stops).**

## What you can do without code

| | |
|---|---|
| **● Record** | Records your own clicks, key presses and timing, then turns them into a "recorded actions" node that replays them once or on repeat (like FRep). Connect a detection to its `run` input for "when X appears, play this". |
| **✂ Cut out an image** | Drag a box around a button or icon in the live view and name it. SightFlow finds it on screen (template matching) with no training. |
| **YOLO-World** | Describe what to find in English words ("enemy character"); open vocabulary, no training. |
| **Trained YOLO** | Load your own `best.pt` for the best accuracy on a specific game. |
| **Colour** | HSV colour ranges, picked from the live view with an eyedropper. |
| **Node editor** | Double-click the canvas (or press `/`) to search and add a node, right-click for menus, `Ctrl+Z` / `Ctrl+Y` to undo/redo. Changes are saved automatically. |
| **Projects** | Create, switch, rename, import and export projects from the top bar. |

Modes: **Stop** / **Watch** (detect and decide, send no input) / **Operate** (really press keys and move the mouse; **F12** stops).

## From source

```bash
git clone https://github.com/aruiki/SightFlow-GameBot.git
cd SightFlow-GameBot
pip install -r requirements.txt          # GPU: install CUDA PyTorch first
cd webui && npm ci && npm run build && cd ..
python -m sightflow web                  # opens http://127.0.0.1:8765 with the demo
```

Tests: `python -m pytest -q`. CI runs the tests, lints and builds the UI, and attaches a
Windows zip to every `v*` tag's release.

## License

SightFlow is MIT. Ultralytics (used for YOLO / YOLO-World) is AGPL-3.0: if you
distribute or serve something that includes it, follow the AGPL terms.
