# SightFlow

**画面の物体検出 × ノードをつなぐだけのローコード**で、ゲームを自動操作するボットを作るツールです。

AI に行動を学習させるのではなく、「何が見えているか」は本物の物体検出器（YOLO-World / YOLO）に任せ、
「どう動くか」はブラウザ上でノードを線でつないで組み立てます。学習データも報酬設計も不要で、
実行中は各ノードに今の値が出るので、どこがおかしいかがすぐ分かります。

```
画面の取り込み ─▶ 検出 + 追跡ID（YOLO-World / YOLO）─▶ ノードの流れ ─▶ キーボード / マウス
                                     ▲                                │
                                     └──── ブラウザにライブ表示 ◀─────┘
```

- 📘 **[使い方ガイド](docs/guide.md)**（起動 → デモ → 最初のボット → 自分のゲームで使う → レシピ）
- 📗 **[ノード一覧](docs/nodes.md)**（全ノードの入力・出力・設定）
- 🖱 **[マクロの作り方](docs/macro.md)**（画面を登録 → 座標クリック → 草むら/敵/ガスの戦術）

## Web UI（ノードエディタ）

詳しい手順は [使い方ガイド](docs/guide.md) にあります。

Node-RED のように**ノードを線でつなぐだけ**でボットの動きを作れます。
検出された物体には**ラベル**（enemy など）と**追跡ID**（#3 など、同じ物体なら同じ番号）が付き、
それを元に「いちばん近い敵を狙う」「同じ敵を追い続ける」といった処理を組み立てます。

```bash
pip install -r requirements.txt
cd webui && npm install && npm run build && cd ..
python -m sightflow web                       # デモ → http://127.0.0.1:8765
python -m sightflow web demo/arena_macro.json  # 草むら/敵の戦術デモ
python -m sightflow web --template brawl_like  # 画面認識マクロのテンプレート
python -m sightflow web projects/mygame.json  # 自分用（なければ新規作成）
```

- **左**: ノード一覧（見る / 選ぶ / 判断 / 操作 / 便利）。ドラッグで置く・ダブルクリックで追加。全ノードの説明は [ノード一覧](docs/nodes.md)
- **中央**: ノードをつなぐキャンバス。線の色がデータの種類（リスト / 物体 / 数 / はい・いいえ）で、合わない線はつながりません。
  実行中は各ノードに**今の値**が出て、実際に動いた操作ノードは**光ります**。変更は自動保存されます
- **右**: ライブ画面（検出枠・ラベル・ID・移動方向・照準）、**画面**（画面の登録・一致度・しきい値）、**学習**（フレーム収集・箱注釈・自動ラベル・YOLO学習）、設定。画面をクリックすると座標がコピーされます
- 上部のモード: **停止** / **見るだけ**（入力を送らない）/ **操作する**（実際にキーとマウスを操作。F12で停止）

### 検出器

| 種類 | 内容 |
|---|---|
| **YOLO-World**（既定） | 探したいものを**英語の言葉で書くだけ**で検出（オープン語彙）。学習不要。RTX 3060 で 1フレーム約20ms |
| 学習済みYOLO | 自分で学習した `best.pt` や YOLO11 などを読み込む。ゲーム専用に学習すれば最も高精度 |
| 色 | HSV の色範囲。付属デモ用 |

YOLO 系はすべて ByteTrack で追跡IDが付きます。初回はモデルの重みが自動でダウンロードされます（`models/`）。
GPU で動かすには CUDA 版 PyTorch を入れてください（例: `pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128`）。

> Windows では、深いフォルダに仮想環境を作ると PyTorch の DLL がパス長制限（260文字）で読み込めないことがあります。
> その場合は `C:\Users\<you>\.venvs\sightflow` のような短いパスに仮想環境を作ってください。

> Ultralytics（YOLO / YOLO-World）は AGPL-3.0 です。SightFlow 本体は MIT ですが、
> Ultralytics を組み込んだものを配布・サービス提供する場合は AGPL の条件に従う必要があります。

### 取り込み元

デモゲーム / ウィンドウ（名前の一部で指定、候補を一覧表示）/ 画面の範囲 / 動画ファイル（録画したプレイで検出を確かめる用）

## YAML で書く（CLI）

Web UI を使わず、YAML のルールで動かすこともできます。

### デモの結果

付属のミニゲーム **Bush Arena**（自作。草むらに隠れながら敵を撃ち、コインを集める）で、
ボットは**描画された画素だけ**を見て操作します。ゲーム内部の状態は読みません。
灰色の壁はあえて検出していません。ボットは「詰まり検知」だけで壁を回り込みます。

| エージェント | 平均スコア | 生存時間 (最大120s) | 撃破 | コイン |
|---|---|---|---|---|
| 何もしない | 0.2 | 16.1 s | 0 | 0.2 |
| ランダム操作 | 8.2 | 24.9 s | 2.6 | 0.4 |
| sightflow（詰まり回避なし） | 134.4 | 120.0 s | 39.6 | 15.6 |
| **sightflow（詰まり回避あり）** | **159.2** | **120.0 s** | **44.0** | **27.2** |

5シード平均。1ティックあたりの検出+判断は約7ms（CPU）。`python demo/bench.py` で再現できます。

### インストール

```bash
pip install -r requirements.txt          # YOLOを使う場合は追加で: pip install ultralytics
```

### すぐ試す

```bash
python demo/bench.py                                         # ヘッドレスでベンチマーク
python -m sightflow run demo/arena_sim.yaml --seconds 60       # 画面なしシミュレーション + デバッグ表示
```

実際のウィンドウを実際のキー入力で操作する場合（Windows）:

```bash
python demo/arena.py                          # ゲームを起動（自分で遊ぶなら WASD + クリック）
python -m sightflow run demo/arena_live.yaml    # 別ターミナルで。F12 で停止
```

> 実行中はマウスとキーボードを奪います。止めるときは **F12**（デバッグ窓なら q / Esc）。
> 先に `--dry-run` で判断だけ確認するのがおすすめです。

### 設定ファイル

```yaml
source: {type: window, title: "my game"}      # window / screen / image / python
input:  {type: keyboard_mouse, move_keys: {up: w, down: s, left: a, right: d}}
anchor: player                                # 距離・方向の基準になるラベル（式中では me）

detectors:
  - {type: color, label: enemy, hsv: [[0,150,120],[8,255,255]], min_area: 150}
  - {type: template, label: chest, image: sprites/chest.png, threshold: 0.85}
  - {type: yolo, model: best.pt, labels: {player: 0, enemy: 1}, conf: 0.4}

flow:
  initial: hide
  global:                                     # 全状態で最初に評価されるルール
    - {when: gameover.any, do: [{key: r}], goto: hide, cooldown: 1.0}
  states:
    hide:
      rules:                                  # 上から順に評価し、最初に真になった1つだけ実行
        - when: enemy.within(300) > 0
          goto: fight
        - do: [{move_toward: bush.nearest}]   # when なし = 常に真
    fight:
      rules:
        - when: not enemy.any
          goto: hide
        - do: [{click: enemy.nearest, cooldown: 0.3}]
```

`extends: base.yaml` で別ファイルを継承・上書きできます（例: `demo/arena_live.yaml`）。

#### 条件式で使えるもの

| 名前 | 意味 |
|---|---|
| `enemy.any` / `enemy.count` | そのラベルが見えているか / 数 |
| `enemy.nearest` / `.farthest` / `.best` | 最も近い / 遠い / スコアが高い検出 |
| `enemy.within(200)` | 基準から200px以内の数 |
| `d.x` `d.y` `d.w` `d.h` `d.score` `d.distance` `d.dx` `d.dy` `d.angle` `d.exists` | 検出ごとの値 |
| `me` | 基準（anchor）の検出。見えないときは画面中央・`exists=False` |
| `state_time` / `t` | 現在の状態に入ってからの秒数 / 経過時間 |
| `vars.名前` | `set` アクションで保存した値（未設定は None） |
| `frame.w` `frame.h` | フレームサイズ |
| `speed` | 自分の実際の移動速度 (px/s)。カメラ追従型のゲームでも背景のスクロールから推定 |
| `stuck_time` | 移動しようとしているのにほぼ動けていない秒数（壁に引っかかった判定に使う） |
| `last_move.x` `last_move.y` | 前のティックで送った移動方向 |
| `min` `max` `abs` `len`、`and` `or` `not`、比較・四則演算 | 使える演算 |

見つからないラベルの `.nearest` は「距離が無限大の空の検出」を返すので、
`enemy.nearest.distance < 100` は例外にならず単に False になります。

式は安全なサブセットだけを許可しています（import・`__xxx__`・任意の関数呼び出しは不可）。

#### アクション

| アクション | 動作 |
|---|---|
| `{key: r}` | キーを1回押す（`space`, `f1` などの特殊キー名も可） |
| `{move_toward: coin.nearest}` / `{move_away: ...}` | 基準から対象へ向かう／離れる。8方向の押しっぱなしキーに変換 |
| `{hold: {key: e, seconds: 0.5}}` | キーを指定秒数押し続ける |
| `{move: [0, -1]}` | 方向ベクトルで移動（式も可: `"[vars.ux, vars.uy]"`） |
| `{click: enemy.nearest}` | 対象の位置をクリック（`[x, y]` も可） |
| `{drag: {from: [x, y], to: 対象, seconds: 0.15}}` | 押したまま移動して離す（スワイプ） |
| `{aim: {button: [0.88, 0.78], target: enemy.nearest, length: 120}}` | タッチ操作の照準。攻撃ボタンから「自分→対象」の方向へ `length` px ドラッグ |
| `{set: {name: 式}}` | `vars` に値を保存 |
| `{log: "text"}` | コンソールに出力 |

どのアクションにも `cooldown: 秒` を付けられます。ルール自体にも `cooldown` があります。
移動系のアクションが実行されなかったティックでは、移動キーは離されます。
状態には `on_enter:` で、入った瞬間に1回だけ実行するアクションも書けます。
座標 `[x, y]` は、両方が 0〜1 なら画面サイズに対する割合として扱います（ウィンドウサイズが変わっても使える）。

#### 壁に引っかかったとき

```yaml
run:
  stuck: {min_speed: 25, camera_motion: true}   # これより遅いと「詰まり」
flow:
  global:
    - {when: stuck_time > 0.4 and state != "unstick", goto: unstick}
  states:
    unstick:                                    # 進もうとした方向の真横へ少し動く
      on_enter: [{set: {ux: "-last_move.y", uy: "last_move.x"}}]
      rules:
        - {when: state_time > 0.6, goto: hide}
        - do: [{move: "[vars.ux, vars.uy]"}]
```

#### 検出器の共通オプション

| オプション | 意味 |
|---|---|
| `roi: [x, y, w, h]` | 画面のこの範囲だけ探す |
| `offset: [dx, dy]` | 結果の位置をずらす（例: HPバーからキャラの体へ） |
| `max_count: n` | 上位n個だけ残す |
| `aspect: [lo, hi]` | （color）幅÷高さの範囲。HPバーのような横長だけ拾う |

### 自分のゲームに使う手順

1. `python -m sightflow grab my.yaml -o shot.png` で1フレーム保存
2. `python -m sightflow pick shot.png` で対象の画素をクリックし、HSVの値を調べる
3. `detectors` を書き、`python -m sightflow detect my.yaml shot.png -o check.png` で枠が正しく付くか確認
4. `flow` を書き、`python -m sightflow run my.yaml --dry-run` でデバッグ表示だけ確認
5. 問題なければ `--dry-run` を外して実行

色で取れないものは `template`（スプライト画像）か `yolo`（自分で学習したモデル）に置き換えます。
独自の検出器は `@sightflow.register("name")` で `Detector` を継承して追加できます。

`examples/brawlstars_practice.yaml` は、エミュレータ上の練習試合（対ボット）を想定した**未調整の雛形**です。
キャラではなくHPバーの色（自分=緑、敵=赤）で検出し、攻撃ボタンからのドラッグで照準します。
数値はすべて自分の画面で測り直す必要があります（手順はファイル末尾）。
なお、このゲームの規約は対戦モードに関係なく自動操作を禁止しているため、使うならサブアカウントにしてください。

## 構成

```
sightflow/
  detection.py   Detection / LabelGroup（条件式から見えるオブジェクト）
  detectors.py   color / template / yolo とプラグイン登録
  screens.py     画面の登録・類似判定（template / pHash / SSIM）とヒステリシス
  dataset.py     フレーム収集・YOLO注釈・自動ラベル・学習
  behavior.py    草むら・敵・ガスの戦術（移動と攻撃のチャネル仲裁）
  expr.py        安全な条件式
  flow.py        状態・ルール・アクション・クールダウン
  io.py          画面キャプチャ（ウィンドウ / 範囲 / 画像 / Python）と入力（pynput）
  motion.py      自分の移動速度の推定と詰まり検知
  tracking.py    追跡ID（YOLO 以外の検出器用）
  graph.py       ノードの定義と実行エンジン（ここに足したノードがエディタに出る）
  server.py      Web UI のサーバー（FastAPI + WebSocket）
  bot.py         設定読み込みとメインループ
  overlay.py     デバッグ表示
  cli.py         run / detect / grab / pick / web
webui/           ノードエディタ（React + React Flow）
demo/            Bush Arena（自作ミニゲーム）・設定・ノードのデモ（arena_project.json）・ベンチマーク
examples/        他のゲーム向けの設定雛形
docs/            使い方ガイド・ノード一覧（nodes.md は自動生成）
tests/
```

## 利用上の注意

オンラインゲーム、特に対人戦での自動操作は、多くの場合利用規約で禁止されており、
アカウント停止の対象になり、他のプレイヤーにも迷惑がかかります。
このツールは、自作ゲーム・オフラインゲーム・自動化が許可された環境での利用を想定しています。

## ライセンス

[MIT](LICENSE)
