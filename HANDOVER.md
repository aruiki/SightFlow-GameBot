# SightFlow 引き継ぎメモ

最終更新: 2026-11-05 / 作成: Cline（worktree `bb918`）
対象リポジトリ: https://github.com/aruiki/SightFlow-GameBot

## 0. 30秒でわかる現在地

- **プロダクト**: 画面の物体検出（YOLO-World / YOLO / 色）と、ブラウザ上のノード配線でゲームを自動操作するツール。Python + FastAPI + React Flow。
- **このworktree** (`cline/bb918`, `3597169`) は **main と完全に同一**。作成時点では作業ツリーはクリーン（現在はこの `HANDOVER.md` だけが未追跡）。
- **実際の最新作業は `feature/one-click-launcher` (`42bdb17`) にあり、main より19コミット先行・1コミット遅れ。まだ origin に push されていない（ローカルのみ）**。
- そのブランチは別worktree `...\.claude\worktrees\gallant-spence-3533d9` にチェックアウト済み（クリーン）。
- feature ブランチのテストは **53 passed / 20.94s**（下記の venv で 2026-11-05 に実行）。
- **最重要リスク**: `models/` `datasets/` `runs/` `projects/` は `.gitignore` 対象で、合計約 **2.2 GB** がローカルにしかない。AI テンプレート（`start-brawlstars-ai.bat`）はこれらがないと動かない。

## 1. ブランチとworktree

| ブランチ | HEAD | 状態 |
|---|---|---|
| `main` / `origin/main` | `3597169` | PR #2（docs/web-guide）までマージ済み |
| `cline/bb918`（このworktree） | `3597169` | main と同一。固有コミットなし |
| `feature/one-click-launcher` | `42bdb17` | **main +19 / -1。origin 未push** |
| `feature/web-node-editor` | `cc0f734` | マージ済み（PR #1） |
| `docs/web-guide` | `a8004cb` | マージ済み（PR #2） |

- feature の worktree: `C:\Users\aruik\AppData\Local\Claude-Data\claude_pzs8sxrjxfjjc\scratch-workspaces\c57a5daf-179d-4f49-8f24-cf772dbc8ddb\d23c8834-f520-4804-a741-1449d009cf05\scratch-2026-11-04-9e7f34\.claude\worktrees\gallant-spence-3533d9`
- main の元worktree: 上記パスの `.claude\worktrees\...` を除いたもの。
- `git merge-tree --write-tree main feature/one-click-launcher` は競合なし（`6c728a2...` を生成）。ただし main 側の docs 変更（`a8004cb`）とは `README.md` / `docs/guide.md` / `docs/nodes.md` が重なるので、マージ後にテスト再実行が必要。

## 2. `feature/one-click-launcher` の内容

差分: **33ファイル / +3,391 −65**。概要は次のとおり。

### 2-1. ワンクリック起動（配布向け）

| 追加 | 内容 |
|---|---|
| `start.bat` | Python 3.10+ を探し、`%USERPROFILE%\.venvs\sightflow` に venv 作成 → GPU があれば CUDA 版 PyTorch、なければ CPU 版 → `requirements.txt` → `python -m sightflow web` → ブラウザ起動。初回だけ数GBDL。環境変数 `SIGHTFLOW_VENV` / `SIGHTFLOW_PYTHON` / `SIGHTFLOW_TORCH` / `SIGHTFLOW_NO_BROWSER` 対応 |
| `start-brawlstars.bat` | `start.bat --template brawlstars` |
| `start-brawlstars-ai.bat` | `start.bat --template brawlstars_ai`（学習済みモデル使用） |
| `webui/dist/**` | ビルド済みUIをコミット（`.gitignore` から `dist` を削除、`.gitattributes` で generated 扱い）。Node.js 不要で配布可能に |

### 2-2. ブロスタ用テンプレート

- `templates/brawlstars.json`（604行・色検出）と `templates/brawlstars_ai.json`（543行・学習済みYOLO + 模倣）
- `docs/brawlstars.md`（159行）: LDPlayer 9 の実画面で調整した手順・HSV の合わせ方・キー割り当て・症状別対処
- `python -m sightflow web --template <name>` を追加。初回は `templates/<name>.json` → `projects/<name>.json` にコピーし、以降はユーザー側を編集
- 既定の取り込み: `source: {type: window, title: LDPlayer, child: TheRender, width: 960}`。幅960に正規化するのでウィンドウサイズ変更に強い

### 2-3. 地形・射線・移動の知能化（`sightflow/terrain.py` 新規367行）

- 画面を16pxのマスに分け、色から **床 / 草むら / 壁 / 不明** を学習（床＝開始時に自分の周りで最多の色＋1秒前の立ち位置、壁＝引っかかったとき目の前の色）
- 新ノード: `line_of_sight`（射線）、`path_toward`（壁を迂回して接近）、`path_away`（行き止まりに逃げない）、`shot_spot`（撃てる位置まで回り込む）
- 移動系の追加: `strafe`（撃ちながら横に回る）、`search`（見失った方向へ探す）、`dodge` に「交互」「回す秒数」、`within` に「最小半径」、`aim` に「右クリック / キー（スマートキャスト）」方式
- 入力安全策: `act_only_with_me`（ゲームが前面のときだけ入力）、`both` 検出器（試合中だけ操作）

### 2-4. 学習パイプライン（手ラベル不要）

| 追加 | 内容 |
|---|---|
| `sightflow/dataset.py`（290行） | `record`（見るだけでフレーム収集）/ `autolabel`（既存検出器でYOLO形式に自動注釈。空フレームのネガティブも追加）/ `train`（YOLO学習） |
| `sightflow/imitate.py`（252行） | プレイ動画から移動・攻撃を逆算（`imitate-extract`）し、方針モデルを学習（`imitate-train`）。`imitate` ノードで利用 |
| CLI 追加 | `record` / `autolabel` / `train` / `imitate-extract` / `imitate-train` |
| `docs/guide.md` | 「5-2b. 自分のゲーム用に YOLO を学習させる（手で印を付けずに）」を追加 |

### 2-5. 取得・入力の修正

- `sightflow/io.py`（+143）: Web UI でウィンドウ/画面キャプチャが `BitBlt access denied` になる問題の修正、スレッド跨ぎの取得、前面判定
- `sightflow/server.py`（+86）: ゲームウィンドウ未起動時は3秒ごとに再探索、`--template` 対応、入力の前面ガード
- `sightflow/graph.py`（+216）: 上記ノード群と地形コンテキスト
- `sightflow/detectors.py`（+50）: YOLO のモデル解決（`models/`）・バックアップ検出器

### 2-6. テスト

新規 `tests/test_templates.py` / `test_terrain.py` / `test_imitate.py` / `test_webui.py` と既存テストの追加。主な検証:

- 全テンプレートが読み込め、ノード設定がスキーマに一致すること（学習モデルがなければ **skip**）
- ブロスタの色検出で「射程内なら撃つ」「中距離なら横に回る」「敵なしなら進む」「足元の赤を無視」
- 地形: 床学習・壁の射線遮蔽・壁の迂回経路・撃てる位置の探索
- 模倣: 特徴量の固定サイズ、学習とノードの動作
- `webui/dist` がリポジトリに存在すること
- `docs/nodes.md` が `python -m sightflow nodes-doc` の出力と完全一致すること（**ノードを足したら再生成必須**）

## 3. 検証済みの実行環境

| 項目 | 値 |
|---|---|
| Python | 3.13.15（`C:\Users\aruik\AppData\Local\Programs\Python\Python313\python.exe`） |
| 仮想環境 | `C:\Users\aruik\.venvs\sightflow`（**短いパス。PyTorch の DLL パス長対策**） |
| PyTorch | 2.11.0+cu128 / `torch.cuda.is_available() == True` |
| ultralytics | 8.4.202 |
| OpenCV | 5.0.0 |
| pygame | 2.6.1 |
| pytest | 9.1.1 |
| fastapi / uvicorn | インストール済み |

> システムの `python` には cv2 等が入っていない。**必ず venv の python を使う**:
> `C:\Users\aruik\.venvs\sightflow\Scripts\python.exe -m pytest -q`

実行結果（feature worktree）:

```
53 passed, 2 warnings in 20.94s
```

参考: この main 相当の worktree でも `31 passed, 1 warning in 11.22s` を確認（feature 側の追加テスト分だけ件数が増える）。

警告は pygame の `pkg_resources` 非推奨と、`sightflow/io.py` の `mss.mss()` 非推奨（将来の mss 更新で要修正）。

## 4. git 管理外のローカル資産（**コピー必須**）

feature worktree 直下に存在。`.gitignore` で `models/` `datasets/` `runs/` `projects/` が除外されている。

### 4-1. `models/`（554.6 MB / 9ファイル）

| ファイル | サイズ | 用途 |
|---|---|---|
| `brawlstars_v1.pt` | 5.3 MB | 初期 YOLO（v1） |
| `brawlstars_v2.pt` | 5.3 MB | プロ動画から学習した YOLO（v2） |
| `brawlstars_v3.pt` | 18.3 MB | **YOLO11s。AI テンプレートが使用**。メニュー誤検出 21/71 → 0/71 |
| `brawlstars_policy.pt` | 0.1 MB | 模倣方針モデル（`imitate` ノード） |
| `yolo11n.pt` / `yolo11s.pt` | 5.4 / 18.4 MB | ベース重み |
| `yolov8s-worldv2.pt` / `yolov8x-worldv2.pt` | 24.7 / 139.6 MB | YOLO-World の重み（自動DL相当） |

学習ログ（`runs/*/results.csv` 最終行、P / R / mAP50 / mAP50-95）:

| run | epochs | P | R | mAP50 | mAP50-95 |
|---|---|---|---|---|---|
| `brawlstars_v1` | 60 | 0.794 | 0.697 | 0.736 | 0.625 |
| `brawlstars_v2` | 80 | 0.863 | 0.802 | 0.857 | 0.764 |
| `brawlstars_v3` | 80 | 0.806 | 0.759 | 0.776 | 0.614 |

> v3 は mAP だけ見ると v2 より低いが、**メニュー画面の誤検出が 0 になった**ため AI テンプレートは v3 を採用している。数値だけで v2 に戻さないこと。

### 4-2. そのほか

| ディレクトリ | サイズ | 内容 |
|---|---|---|
| `datasets/brawlstars/` | 1.61 GB / 19,639ファイル | `set_ld`（LDPlayer 276枚）、`set_video`（プロ動画 1,842枚）、`set_vneg`（ネガティブ448）、`set_hf`（306）、`set1`（209）、`hf_bp`（HFデータセットのキャッシュ） |
| `runs/` | 73.9 MB | `brawlstars_v1` / `v2` / `v3` の重み・曲線・`results.csv` |
| `projects/` | 4ファイル | 実機で調整した `brawlstars.json`、`brawlstars_ai.json`、旧版 `brawlstars.old*.json` |

`templates/brawlstars_ai.json` は `brawlstars_v3.pt` と `models/brawlstars_policy.pt` を参照する。
**これらがない環境では `start-brawlstars-ai.bat` は動かず、`tests/test_templates.py` の該当ケースは skip される**（色ベースの `start-brawlstars.bat` はモデル不要で動く）。

## 5. 起動と使い方

```bat
rem デモ（初回は venv 作成＋数GBのインストール）
start.bat
rem 自分用プロジェクト（なければ空で作成）
start.bat projects\mygame.json --port 9000
rem テンプレートから新規作成
start.bat --template brawlstars
start-brawlstars.bat        rem = start.bat --template brawlstars（色検出・モデル不要）
start-brawlstars-ai.bat     rem = start.bat --template brawlstars_ai（models/ 必須）
```

- UI: http://127.0.0.1:8765 / モードは **停止・見るだけ・操作する**。**F12** で非常停止。
- `webui/src` を変えたら `cd webui; npm install; npm run build` し、**`webui/dist` もコミット**する。
- ノードを追加・変更したら `python -m sightflow nodes-doc > docs/nodes.md` で再生成（テストが一致を強制）。
- ドキュメント: `docs/guide.md`（使い方）、`docs/nodes.md`（自動生成）、`docs/brawlstars.md`（ブロスタ手順）。

## 6. 既知の注意点・リスク

1. **feature ブランチが origin に存在しない**。このPCが壊れると19コミットが失われる。最優先で push する。
2. **学習資産（約2.2GB）が git 管理外**。別環境へは手動コピーが必要。再現するなら `record → autolabel → train` をやり直す（`docs/guide.md` 5-2b）。
3. **ブロスタの利用規約は全モードで自動操作を禁止**。練習試合でもアカウント停止リスクがある。`docs/brawlstars.md` と README に明記済み。サブアカウント・自己責任の前提を崩さない。
4. **Ultralytics は AGPL-3.0**。SightFlow 本体は MIT だが、Ultralytics 組み込みで配布・サービス提供する場合は AGPL 条件に従う。
5. **PyTorch の DLL パス長問題**（Windows 260文字）。venv は必ず短いパスに。`start.bat` は既定でそうする。
6. **`docs/nodes.md` は自動生成**。手で編集しない。
7. `sightflow/io.py` の `mss.mss()` は非推奨。将来 `mss.MSS` へ移行が必要。
8. テストはモデル・テンプレートの存在に依存して skip するため、**CI を入れるならモデルなしでも通る範囲**（skip 許容）で組む。現状 `.github/` はなく CI 未設定。
9. `projects/` は自動保存で上書きされる。手で編集するときは SightFlow を閉じる（`docs/brawlstars.md` に記載）。

## 7. 次にやるべきこと（推奨順）

1. **バックアップ**: feature ブランチを push する。
   ```bat
   git push -u origin feature/one-click-launcher
   ```
2. **main の取り込み**: feature worktree で `git merge main`（`merge-tree` では競合なしを確認済み。docs 3ファイルは要確認）→ テスト → push → PR 作成。
3. **実機確認**: LDPlayer 9 + ブロスタ練習試合で `start-brawlstars.bat` と `start-brawlstars-ai.bat` を「見るだけ」→「操作する」の順で再確認。`docs/brawlstars.md` の数値（HSV・offset・距離しきい値）が現行画面と合っているか。
4. **学習手順の再現性確認**: まっさらな環境で `record → autolabel → train → imitate-extract → imitate-train` を通し、`brawlstars_v3.pt` / `brawlstars_policy.pt` 相当が作れるか。モデル配布（GitHub Releases 等）も検討。
5. **ドキュメント更新**: `docs/nodes.md` 再生成、README のテンプレート説明、既知の制限（ウィンドウ名・管理者権限・エミュレータ差異）。
6. **品質**: CI（pytest + `nodes-doc` 一致 + `webui/dist` 存在）を追加。`mss` 非推奨対応。

## 8. この引き継ぎで実行した検証

```powershell
# ブランチ・差分
git log --all --graph --oneline
git rev-list --left-right --count main...feature/one-click-launcher   # => 1  19
git merge-tree --write-tree main feature/one-click-launcher            # => 競合なし

# テスト（feature worktree）
cd <feature worktree>
C:\Users\aruik\.venvs\sightflow\Scripts\python.exe -m pytest -q          # => 53 passed, 2 warnings in 20.94s

# テスト（このworktree = main 相当）
cd C:\Users\aruik\.cline\worktrees\bb918\scratch-2026-11-04-9e7f34
C:\Users\aruik\.venvs\sightflow\Scripts\python.exe -m pytest -q          # => 31 passed, 1 warning in 11.22s
```

> この `HANDOVER.md` はまだ未追跡（`?? HANDOVER.md`）。残す場合は `git add HANDOVER.md && git commit` するか、feature ブランチ側へコピーする。

## 9. 2026-11-05 追加（要件・戦術プロトタイプ）

- `docs/requirements/webui.md` — WebUI 要件定義 v0.3（画面認識マクロビルダー版、796行）
  - 製品定義、機能要件（検出 / 画面類似・遷移 / クリック・キー / 状態フロー）、API、NFR、ギャップ、受け入れ基準
  - §15 完成目標シナリオ（Brawl Stars 類似アプリ）、§16 並列実行モデル、§17 行動アルゴリズム、§18 実装状況
- `sightflow/behavior.py` — 戦術プロトタイプ（新規）
  - 草むら選択（安全・広さ・距離・敵脅威のスコア + スティッキー）
  - 16方向ステアリング（目標・ガス余裕・敵回避・画面端・詰まりを1スコアに統合）
  - ガス距離場（ボックス / マスク + 距離変換）、攻撃チャネル（移動と独立）
- `tests/test_behavior.py` — 15ケース（草むら・ガス・並列攻撃・待機・緊急回避・詰まり・ヒステリシス）
- `demo/policy_demo.py` — Bush Arena を描画ピクセルだけで走らせる統合確認

検証（2026-11-05）:

```
pytest -q                          => 46 passed, 1 warning in 21.92s
demo/policy_demo.py (seed 1, 30s)  => 草むら滞在 99%, 初回 0.4s, 攻撃 26, 撃破 6, HP5
```

> これらは未コミット。`feature/one-click-launcher` とは独立した追加分（main 相当の worktree 上）。

### 9-1. 画面認識マクロ一式（同日追加）

| 追加/変更 | 内容 |
|---|---|
| `sightflow/screens.py` | 画面テンプレート / pHash / SSIM、ROI、ヒステリシス、登録・更新・削除・永続化 |
| `sightflow/graph.py` | 画面ノード（`screen_is` / `screen_score` / `screen_changed` / `wait_screen`）、`click_at`、`hold_key` / `release_key`、`wait`、`tactics` |
| `sightflow/server.py` | 毎ティックの画面認識、WSに画面スコア、`/api/screens` CRUD・テスト・再取得、`/api/models`、`/api/capture`、キーdown/upと停止時解放、`--template` |
| `sightflow/io.py` | `key_down` / `key_up` / `release_all` |
| `webui/src/ScreenPanel.jsx` | 画面タブ（登録・一覧・一致度・しきい値・ROI・削除） |
| `webui/src/LivePanel.jsx` | 現在の画面名/一致度、検出結果一覧 |
| `webui/dist` | ビルド済みUI（`.gitignore` から dist を解除） |
| `demo/arena_macro.json` | 草むら/敵/ガスの戦術デモ |
| `templates/brawl_like.json` | 対象アプリ用テンプレート（画面ゲート＋クリック＋戦術） |
| `docs/macro.md` | 画面登録→マクロ完成の手順 |
| `tests/test_screens.py` / `test_screen_nodes.py` / `test_server_screens.py` / `test_arena_macro.py` | 画面類似・ノード・API・デモ結合 |

```
pytest -q  => 94 passed, 1 warning（環境により約50s）
python -m sightflow web demo/arena_macro.json     # デモで戦術
python -m sightflow web --template brawl_like     # 対象アプリ用
```

> 対象アプリの実体はワークスペースに無かったため、Bush Arena を代役に一気通貫で検証。
> 実アプリでは画面タブで `title` `mode` `lobby` `match` `result` を登録し、座標・HSV・攻撃キーを合わせる。

### 9-3. P3 仕上げ（同日追加）

| 追加/変更 | 内容 |
|---|---|
| `sightflow/detection.py` | `region` / `polygon` を持つ領域検出、`to_dict` にポリゴン |
| `sightflow/detectors.py` | `region`（HSVポリゴン）検出器、ラベル別 `label_conf` フィルタ、ROI/offsetのポリゴン補正 |
| `sightflow/behavior.py` | ポリゴン内外判定、ポリゴン面積、GasFieldのポリゴン→マスク距離場 |
| `sightflow/screens.py` | 1画面あたり**複数テンプレート**（最大スコア）、追加/削除・永続化 |
| `sightflow/dataset.py` | `FrameRecorder`（一定間隔の自動収集） |
| `sightflow/server.py` | `/api/screens/{id}/templates`、`/api/frame`、`/api/dataset/record`、ラベル別しきい値 |
| `webui/src/ScreenPanel.jsx` | ROIドラッグ、テンプレート追加/削除、枚数表示 |
| `webui/src/DatasetPanel.jsx` | 自動収集の開始/停止・間隔・収集数、`data.yaml` 書き出し |
| `webui/src/LivePanel.jsx` | 領域（ポリゴン）の描画、一時停止・1フレーム送り |
| `webui/src/SettingsPanel.jsx` | 領域検出タイプ、ラベル別しきい値、モデル一覧 |
| `tests/test_region.py` / `test_dataset.py` / `test_server_dataset.py` / `test_screens.py` | 領域6件、自動収集、複数テンプレート |

- デモの草むらも `region` 検出に変更し、ポリゴンでの隠れ判定を検証
- 残り: 動画のコマ送りUI、セグメンテーション注釈、OCR、対象アプリのキャリブレーション


### 9-2. P2 学習パイプライン（同日追加）

| 追加/変更 | 内容 |
|---|---|
| `sightflow/dataset.py` | フレーム保存（train/val自動分割）、YOLOボックス、自動ラベル、`classes.json` / `data.yaml`、`train_yolo` |
| `sightflow/server.py` | `/api/dataset`（capture/frame/labels/autolabel/export）、`/api/train`（バックグラウンド学習・進捗） |
| `webui/src/DatasetPanel.jsx` | 学習タブ（キャプチャ、フレーム一覧、箱描画、クラス変更、自動ラベル、学習フォーム、進捗） |
| `webui/src/App.jsx` / `index.css` | 学習タブ追加、注釈・学習のスタイル |
| `tests/test_dataset.py` / `test_server_dataset.py` | データ層7件、API3件（学習はフェイクで検証） |
| `docs/macro.md` / `docs/guide.md` | 学習タブの手順を追記 |

- データは `datasets/<name>/`、学習結果は `runs/`、モデルは `models/<name>.pt`
- `datasets/` `runs/` `projects/` は `.gitignore` 済み
- 学習は `yolo11n.pt` などから開始。GPU推奨


## 10. 参考リンク

- `README.md` — 全体像・YAML CLI・ベンチマーク
- `docs/guide.md` — 起動から最初のボットまで
- `docs/nodes.md` — 全ノード（自動生成）
- `docs/brawlstars.md` — ブロスタ用テンプレートの調整方法
- `templates/brawlstars.json` / `templates/brawlstars_ai.json` — 実設定
- `demo/arena_project.json` — デモ（Bush Arena）
