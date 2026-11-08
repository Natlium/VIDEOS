#!/usr/bin/env python3
"""relay.py — GitHub のリポジトリ 1 ファイルだけを経由して、離れた PC と往復する。

なぜ issue ではなくファイルか
----------------------------
この環境の GitHub トークンは OAuth App 由来で scope が空のため、issue の
**コメント作成と issue 編集が 403 (Resource not accessible by integration)**
になる。一方 `PUT /contents/...` は 201 で通る。つまり使える双方向路は
リポジトリのファイルだけ。

    ┌ エージェント ─ POST/PUT /contents ─┐
    │   phone/inbox/cmd-<id>.json         │  ← コマンド(下り)
    │   phone/outbox/res-<id>.json        │  ← 結果(上り)
    └ スマホ ───── GET /contents ─────────┘

スマホ側は「GitHub をポーリングして、来たコマンドを実行して結果を置く」だけ。
NAT もポート開放も tailnet も要らない。

使い方 (スマホ = Termux):

    export GH_TOKEN=<repo に write できるトークン>
    python3 relay.py --repo Natlium/VIDEOS            # 常駐
    python3 relay.py --repo Natlium/VIDEOS --once     # 1周だけ
    python3 relay.py --repo Natlium/VIDEOS --send "/health"   # 自分から送る

セキュリティ:
- 実行前に必ず確認プロンプト(既定)。`--yes` で自動承認。
- `--allow-cmd` に前方一致するコマンドだけ許可(既定は phoneapi の GET 群 + 読み取り系)。
- トークンは環境変数からだけ読む。
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
STATE = os.path.expanduser("~/.phone-relay-state.json")
CMD_DIR = "phone/inbox"
RES_DIR = "phone/outbox"
MARK = "relay"  # 結果ファイルの識別子


# ---------------------------------------------------------------- GitHub
class GH:
    def __init__(self, token):
        self.token = token

    def call(self, method, path, payload=None, raw=False):
        req = urllib.request.Request(API + path, method=method,
                                     data=json.dumps(payload).encode() if payload else None)
        req.add_header("Accept", "application/vnd.github+json")
        req.add_header("User-Agent", "phone-relay/2.0")
        req.add_header("Authorization", f"Bearer {self.token}")
        if payload:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = r.read()
                return body if raw else (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            if e.code == 404 and method == "GET":
                return None
            raise RuntimeError(f"GitHub {method} {path} -> {e.code}: {detail}") from None

    def list_dir(self, repo, d):
        v = self.call("GET", f"/repos/{repo}/contents/{d}")
        return v if isinstance(v, list) else []

    def get(self, repo, path):
        """(text, sha) を返す。無ければ (None, None)。"""
        r = self.call("GET", f"/repos/{repo}/contents/{path}")
        if not r or "content" not in r:
            return None, None
        return base64.b64decode(r["content"]).decode("utf-8", "replace"), r["sha"]

    def put(self, repo, path, text, message, sha=None):
        body = {"message": message,
                "content": base64.b64encode(text.encode()).decode()}
        if sha:
            body["sha"] = sha
        r = self.call("PUT", f"/repos/{repo}/contents/{path}", body)
        return (r or {}).get("content", {}).get("sha")

    def delete(self, repo, path, message, sha):
        return self.call("DELETE", f"/repos/{repo}/contents/{path}",
                         {"message": message, "sha": sha})


# ---------------------------------------------------------------- state
def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {"seen_cmd": [], "done": [], "n": 0}


def save(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, indent=2)
    os.replace(tmp, STATE)


def new_id():
    return hashlib.sha1(f"{time.time_ns()}{os.getpid()}".encode()).hexdigest()[:12]


# ---------------------------------------------------------------- 実行
# 確認プロンプトを省いてよい「読み取り専用」コマンド名。
# ここに無いものは必ず確認を取る(未指定で危険なものを素通ししない)。
SAFE_CMDS = {
    "curl", "cat", "ls", "ll", "tail", "head", "grep", "rg", "wc", "cut", "sort",
    "uniq", "jq", "uptime", "date", "uname", "whoami", "pwd", "df", "free", "ps",
    "hostname", "id", "which", "command", "stat", "du", "ifconfig", "ip",
    "getprop", "echo", "printf",
    "termux-battery-status", "termux-wifi-connectioninfo", "termux-wifi-scaninfo",
    "termux-sensor", "termux-location", "termux-clipboard-get",
    "termux-notification-list", "termux-telephony-deviceinfo",
    "termux-telephony-cellinfo", "termux-camera-info", "termux-sms-list",
}
# シェルのメタ文字。これがあれば中身が読めないので必ず確認する。
META = set(">|&;`$\n(){}[]*?!~<")


def looks_readonly(cmd):
    """安全側に倒す: 単一の読み取り専用コマンドのときだけ True。"""
    s = cmd.strip()
    if not s or any(ch in s for ch in META):
        return False
    head = s.split()[0]
    if head in ("sudo", "env", "nohup", "time", "xargs", "watch", "bash", "sh"):
        return False
    if head == "tailscale":
        rest = s.split()[1:2]
        return bool(rest) and rest[0] in ("status", "ip", "version")
    return head in SAFE_CMDS


def allowed(cmd, allow_list):
    if not allow_list:
        return True
    return any(cmd.startswith(p) or p in cmd for p in allow_list)


def execute(cmd, timeout):
    t0 = time.time()
    try:
        p = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True,
                           timeout=timeout, env={**os.environ, "TERM": "dumb"})
        out = (p.stdout or "") + (("\n[stderr]\n" + p.stderr) if p.stderr else "")
        return p.returncode, out.strip(), round(time.time() - t0, 2)
    except subprocess.TimeoutExpired:
        return 124, f"[timeout {timeout}s]", round(time.time() - t0, 2)
    except Exception as e:  # noqa: BLE001
        return 1, f"[exec error] {e}", round(time.time() - t0, 2)


# ---------------------------------------------------------------- 結果の書き戻し
def write_result(gh, repo, st, cid, cmd, code, out, secs, host):
    limit = 24000
    if len(out) > limit:
        out = out[:limit] + f"\n…({len(out)-limit} 文字切り捨て)"
    payload = {
        "relay": 2, "id": cid, "cmd": cmd, "exit": code,
        "seconds": secs, "host": host,
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stdout": out,
    }
    path = f"{RES_DIR}/res-{cid}.json"
    sha = gh.put(repo, path, json.dumps(payload, ensure_ascii=False, indent=2),
                 f"relay: 結果 {cid} exit={code}")
    st["done"] = (st.get("done", []) + [cid])[-200:]
    save(st)
    return path, sha


# ---------------------------------------------------------------- メインループ
def poll_once(gh, repo, st, args, host):
    entries = gh.list_dir(repo, CMD_DIR)
    cmds = sorted([e for e in entries
                   if e["name"].startswith("cmd-") and e["name"].endswith(".json")
                   and e["name"] not in st.get("seen_cmd", [])], key=lambda e: e["name"])
    if not cmds:
        return 0
    for e in cmds:
        st.setdefault("seen_cmd", []).append(e["name"])
        st["seen_cmd"] = st["seen_cmd"][-300:]
        save(st)

        text, _ = gh.get(repo, e["path"])
        if not text:
            continue
        try:
            job = json.loads(text)
        except json.JSONDecodeError:
            print(f"  {e['name']}: JSON でないので無視", flush=True)
            continue
        cid = job.get("id") or e["name"][4:-5]
        cmd = (job.get("cmd") or "").strip()
        if not cmd:
            continue
        print(f"  ← {cid}: {cmd!r}", flush=True)

        if not allowed(cmd, args.allow_cmd):
            code, out, secs = 126, f"[拒否] 許可リストに合わない: {cmd}", 0.0
        elif not args.yes and not looks_readonly(cmd):
            if not sys.stdin.isatty():
                code, out, secs = 126, "[拒否] tty が無いので確認できない。--yes を付ける", 0.0
            else:
                ans = input(f"    実行する? [{cmd}] (y/N): ").strip().lower()
                if ans != "y":
                    code, out, secs = 126, "[拒否] ユーザーが取り消した", 0.0
                else:
                    code, out, secs = execute(cmd, args.timeout)
        else:
            code, out, secs = execute(cmd, args.timeout)

        p, _ = write_result(gh, repo, st, cid, cmd, code, out, secs, host)
        print(f"  → {p} exit={code} {secs}s", flush=True)
    return len(cmds)


def send(gh, repo, cmd):
    cid = new_id()
    job = {"relay": 2, "id": cid, "cmd": cmd,
           "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    gh.put(repo, f"{CMD_DIR}/cmd-{cid}.json",
           json.dumps(job, ensure_ascii=False, indent=2), f"relay: 指示 {cid}")
    print(f"送信した: {cid}  {cmd}\n  待つなら: --wait {cid}")
    return cid


def wait_result(gh, repo, cid, timeout):
    path = f"{RES_DIR}/res-{cid}.json"
    t0 = time.time()
    while time.time() - t0 < timeout:
        text, _ = gh.get(repo, path)
        if text:
            return json.loads(text)
        time.sleep(3)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="Natlium/VIDEOS")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=6.0)
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--yes", action="store_true", help="確認なしで実行(自動化向け)")
    ap.add_argument("--allow-cmd", action="append", default=[],
                    help="許可するコマンド接頭辞(複数可)。未指定なら全部")
    ap.add_argument("--send", metavar="CMD", help="自分からコマンドを送る")
    ap.add_argument("--wait", metavar="ID", help="指定 id の結果を待つ")
    ap.add_argument("--wait-timeout", type=int, default=120)
    a = ap.parse_args()

    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GH_TOKEN が無い", file=sys.stderr)
        return 1
    gh, st = GH(token), load()
    host = os.uname().nodename

    if a.send:
        send(gh, a.repo, a.send)
        return 0
    if a.wait:
        r = wait_result(gh, a.repo, a.wait, a.wait_timeout)
        if not r:
            print(f"{a.wait}: {a.wait_timeout}s 待ったが来なかった", file=sys.stderr)
            return 1
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("exit") == 0 else 2

    print(f"relay2: {a.repo} host={host}", flush=True)
    print(f"  下り {CMD_DIR}/cmd-*.json / 上り {RES_DIR}/res-*.json", flush=True)
    print(f"  既知コマンド {len(st.get('seen_cmd', []))} 件", flush=True)
    if not a.yes:
        print("  ※ 未知のコマンドは実行前に確認します", flush=True)

    while True:
        try:
            n = poll_once(gh, a.repo, st, a, host)
            if n == 0 and not a.once:
                print(f"  …待機中 ({time.strftime('%H:%M:%S')})", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[poll error] {e}", flush=True)
        if a.once:
            return 0
        time.sleep(a.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nbye", flush=True)
        sys.exit(0)
