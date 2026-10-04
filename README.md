# From Silicon to Galaxy — Samsung Galaxy concept film

半導体のチップから、多層基板、組み立て、完成したスマートフォンへ。細部を積み重ねて一台の形にする流れを描いた、オリジナルのコンセプト広告です。

## 納品物

- **`samsung-galaxy-precision-ad.mp4`** — 約22.5秒、1920×1080、24fps、H.264/AAC
- **`samsung-galaxy-precision-ad-poster.jpg`** — 動画のエンドカード
- **`scripts/render_ad.py`** — 手続き型モーショングラフィックスとオリジナルのアンビエント音を生成するソース

### 映像の流れ

1. **シリコン** — チップの細部から始まる
2. **基板** — 回路と部品を重ねる
3. **組み立て** — ディスプレイ、基板、バッテリー、筐体がひとつになる
4. **完成形** — 前面・背面のプロダクトショット
5. **エンドカード** — 「技術を、あなたの手の中に。」

映像内の端末・部品・製造表現はコンセプトビジュアルです。特定のSamsung製品、実際の製造工程・仕様を再現したものではなく、Samsung公式の広告でもありません。

## 再生成

Python 3.10以降とFFmpegバイナリを含む `imageio-ffmpeg` を使います。日本語書体のIPAexゴシックとライセンス文は `assets/` に同梱しています。

```bash
python -m pip install -r requirements.txt
python scripts/render_ad.py
```

出力先を変える場合は `--output` と `--poster` を指定できます。
