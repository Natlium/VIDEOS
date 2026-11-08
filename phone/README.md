# phone/ — Termux:API を curl で叩く

スマホ(Termux)側で動かす小さな HTTP サーバー。`termux-*` コマンドを JSON API に変換する。
依存は Python 標準ライブラリのみ。

```
phone/
├── server.py   # 本体 (0.0.0.0:8080, ルートは / に列挙)
└── start.sh    # Termux 用ランチャー(依存チェック + wake-lock + IP 表示)
```

## セットアップ (Termux)

```bash
pkg install python termux-api        # Termux:API アプリ本体も別途インストール
pkg install tailscale && tailscaled &   # tailnet 経由で叩く場合
tailscale up

git clone <this repo> && cd VIDEOS/phone
bash start.sh
```

`start.sh` が表示する `http://100.x.y.z:8080` が tailnet アドレス。
LAN 内だけなら `http://192.168.x.x:8080` でもよい。

## 動作確認 (PC / 同じ tailnet の端末から)

```bash
PHONE=100.64.0.5

curl -s -m 3 http://$PHONE:8080/health          # 最初はこれ
curl -s http://$PHONE:8080/status | jq          # バッテリー/Wi-Fi/温度/IP まとめ
curl -s http://$PHONE:8080/battery
curl -s http://$PHONE:8080/wifi
curl -s "http://$PHONE:8080/sensors?name=accel"
curl -s http://$PHONE:8080/location
curl -X POST "http://$PHONE:8080/tts?text=$(printf 'テスト' | jq -sRr @uri)"
curl -X POST http://$PHONE:8080/vibrate?ms=300
curl -X POST "http://$PHONE:8080/notify?title=hi&text=from%20curl"
curl -s http://$PHONE:8080/photo -o shot.jpg    # 撮ってその場で保存
```

ワンライナーで見張る:

```bash
watch -n2 "curl -s http://$PHONE:8080/status | jq -c '{b:.battery.percentage,t:.cpu_temp_c,d:.disk.used_pct}'"
```

## エンドポイント

| method | path | 中身 |
|---|---|---|
| GET | `/health` | 死活確認(最速・無駄な termux 呼び出しなし) |
| GET | `/status` | battery + wifi + tailscale IP + 温度 + disk + uptime |
| GET | `/battery` `/wifi` `/location` `/clipboard` | そのまま JSON |
| GET | `/sensors[?name=accel]` | 一覧 / 1回読み |
| GET | `/photo[?cam=0]` | JPEG を直接返す |
| GET | `/file?path=` | 任意ファイル(ストレージ疎通確認) |
| POST | `/tts?text=` `/notify?title=&text=` `/vibrate?ms=` `/torch?on=1` | 操作系 |

## トークン

tailnet 内とはいえ認証を付けたい場合:

```bash
PHONE_API_TOKEN=s3cret bash start.sh
curl -H "X-Token: s3cret" http://$PHONE:8080/health
```

未設定なら認証なし(従来どおり)。`?token=` クエリでも可。

## ハマりどころ

- **`termux-api が見つからない`** → `pkg install termux-api`(CLI)と Termux:API アプリ本体の両方が必要。片方だけだと `termux-battery-status` が exit≠0 になる。
- **スリープで止まる** → `bash start.sh` は `termux-wake-lock` を打つ。それでも止まるなら Termux の電池最適化をOFFに。
- **後ろから curl が返らない** → Termux の `sshd` ではなくこの HTTP サーバーを使っているか確認。ポートは `-p` / `PORT=` で変更可。
- **`/photo` が 502** → カメラ権限。Termux:API アプリにカメラ権限を付与。
- **`--demo`** → termux-api 未導入でも全ルートの配線を確認できるダミーモード。

## 検証済み (このリポジトリの作業環境)

`--demo` で起動し、全 14 エンドポイント + 認証(401/200)+ 404/400 のステータスを curl で確認済み。

## エージェントと繋ぐ

この sandbox からは tailnet に入れない（`controlplane.tailscale.com` が TLS で切られる）ため、
「エージェント → スマホ」の直結は不可能。代わりに **GitHub のリポジトリ 1 ファイル**を
経由する双方向リレーがある。詳細と実測ログは [RELAY.md](./RELAY.md)。

```bash
export GH_TOKEN=<Contents:write つきトークン>
python3 relay.py --repo Natlium/VIDEOS        # 常駐。下りコマンドを拾って実行し結果を返す
```
