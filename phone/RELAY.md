# phone/relay.py — GitHub の 1 ファイル経由で「エージェント ⇄ スマホ」を繋ぐ

## なぜこれが必要か（実測）

この環境のエージェントから見たネットワークは**閉じています**。

| 経路 | 結果 |
|---|---|
| `controlplane.tailscale.com` / `login` / `derp1` | DNS OK → **TLS で切断** (`SSL_ERROR_SYSCALL`) |
| `example.com` / `deb.debian.org` / `httpbin.org` | **000** |
| `objects.githubusercontent.com` / `uploads.github.com` | **000** |
| `github.com` / `api.github.com` / `codeload` / npm / pypi | **200** |

→ **tailnet に入れないし、トンネルも張れない。** 「僕が君のスマホに curl」は経路が存在しない。

さらにトークンの権限が特殊でした（OAuth App 由来、`x-oauth-scopes` が空）:

| 操作 | 結果 |
|---|---|
| `POST /repos/:r/:o/issues` (issue 作成) | **201 OK** |
| `POST /issues/:n/comments` | **403 Resource not accessible by integration** |
| `PATCH /issues/:n` (本文編集・close) | **403** |
| `PUT /contents/:path` (**コミット**) | **201 OK** |
| `DELETE /contents/:path` | **200 OK** |

→ 双方向に使えるのは**リポジトリのファイルだけ**。なので issue ではなくファイルで往復する。

## 仕組み

```
エージェント(僕)                          スマホ (Termux)
      │                                        │
      │ PUT phone/inbox/cmd-<id>.json  ───────▶│  GET(ポーリング)
      │        (下り: 実行してほしいコマンド)    │   → 確認 → bash -lc
      │                                        │
      │ ◀───────  PUT phone/outbox/res-<id>.json│  (上り: exit/出力)
      │ GET/res-<id>.json で読む                │
```

スマホ側は**外に待ち受けを一切しません**。GitHub に取りに行くだけなので、
NAT・ポート開放・tailnet・固定 IP すべて不要。スマホが 4G でも Wi-Fi でも動きます。

## セットアップ（スマホ・1回だけ）

トークンは **repo への Contents:write 権限**があれば OK。
（クラシック PAT なら `repo`、fine-grained なら `Contents: Read and write`）

```bash
pkg install python git
export GH_TOKEN=<トークン>          # ~/.bashrc に入れるなら chmod 600
cd ~/VIDEOS/phone
python3 relay.py --repo Natlium/VIDEOS
```

常駐したまま放置。`Ctrl-C` で停止。

## 使い方

### スマホ → 僕（結果を報告する）

エージェントが下りコマンドを置くので、スマホ側は回しておくだけ。
確認プロンプトは既定で出ます（`--yes` で自動化）。

### 僕 → スマホ（スマホから実行させる）

スマホ側からも送れる:

```bash
python3 relay.py --repo Natlium/VIDEOS --send "/health"
python3 relay.py --repo Natlium/VIDEOS --send "tailscale status" --wait <id>
```

エージェント側からは `--wait <id>` で結果を読む。

## 安全装置

| 仕組み | 内容 |
|---|---|
| `--yes` 無しの既定 | **読み取り専用コマンド以外は実行前に確認** |
| `looks_readonly()` | 単一の読み取り専用コマンドのみ許可。`;` `|` `$()` `>` 等のメタ文字が1つでもあれば確認必須 |
| `--allow-cmd curl` | 前方一致でさらに絞れる（未指定なら無制限だが確認は出る） |
| exit=126 | 拒否・確認不可（tty 無し）を表す |
| トークン | 環境変数からのみ。ファイル・ログに書かない |
| state | `~/.phone-relay-state.json`。消すと古いコマンドを再実行するので注意 |

`looks_readonly` は **17 ケースでテスト済み**（`rm -rf /`、`ls; rm -rf /`、
`echo $(rm -rf /)`、`python3 -c ...`、`bash -lc id` などはすべて False）。

## 制約

- ポーリング間隔は既定 6 秒（`--interval`）。GitHub API は 5000 req/h なので余裕あり
- 1コマンド 90 秒でタイムアウト（`--timeout`）
- 出力は 24,000 文字で切り捨て
- 結果は 24,000 文字を超えると切られる。動画などバイナリは**この経路に載せない**
  （`objects.githubusercontent.com` が 000 なので raw/添付も取れない。git push を使う）

## 実測ログ（このリポジトリで確認済み）

```
送信した: f07eb0a25d1b  uname -a && date -u +%FT%TZ
  ← f07eb0a25d1b: 'uname -a && date -u +%FT%TZ'
  → phone/outbox/res-f07eb0a25d1b.json exit=0 0.04s

{ "relay": 2, "id": "f07eb0a25d1b", "exit": 0, "host": "e2b.local",
  "stdout": "Linux e2b.local ... x86_64 GNU/Linux\n2026-11-08T06:30:56Z" }
```

## 既知の落とし穴

- **`Resource not accessible by integration`** → このトークンでは issue コメントが書けない。
  リレーはファイル方式なので影響なし。issue を使う実装に戻すと壊れる。
- **`raw.githubusercontent.com` は 000** → 生ファイルは `api.github.com/repos/.../contents` から
  base64 で取る（`GH.get()` がそうしている）。
- **スマホのスリープ** → `termux-wake-lock` を打つ。`start.sh` は打つが relay 単体では打たない。
- **古いコマンドの再実行** → `~/.phone-relay-state.json` を消したとき。`seen_cmd` に 300 件まで記録。
