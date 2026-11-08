#!/data/data/com.termux/files/usr/bin/bash
# Termux でこれを実行するだけ。Termux:API の用意からサーバー起動まで面倒を見る。
#
#   bash start.sh              # 8080 で起動
#   PORT=9000 bash start.sh
#   bash start.sh --demo       # termux-api 無しで骨組みだけ確認
set -u
cd "$(dirname "$0")" || exit 1
PORT="${PORT:-8080}"
BIND="${BIND:-0.0.0.0}"

have() { command -v "$1" >/dev/null 2>&1; }

if [ "${1:-}" != "--demo" ]; then
  echo "== 依存チェック =="
  have python3 || { echo "python3 が無い: pkg install python"; exit 1; }
  if ! have termux-battery-status; then
    echo "termux-api が無い -> pkg install termux-api"
    echo "  ※ Termux:API アプリ本体(F-Droid/Play)も別途インストールが必要"
    echo "  とりあえず骨組みだけ見るなら: bash start.sh --demo"
    exit 1
  fi
  if ! have tailscale; then
    echo "ヒント: tailscale 未導入。tailnet 経由で叩くなら pkg install tailscale && tailscaled"
  fi
fi

# Termux はスリープでプロセスが止まりやすいので起こしておく
have termux-wake-lock && termux-wake-lock 2>/dev/null

echo "== IP 確認 =="
have tailscale && echo "  tailnet: $(tailscale ip -4 2>/dev/null | head -1)"
echo "  LAN    : $(ifconfig 2>/dev/null | grep -oE 'inet ([0-9]{1,3}\.){3}[0-9]{1,3}' | awk '{print $2}' | grep -vE '^127\.' | head -3 | tr '\n' ' ')"

echo "== 起動: :$PORT =="
exec python3 server.py -p "$PORT" --bind "$BIND" "$@"
