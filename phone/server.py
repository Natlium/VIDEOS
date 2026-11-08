#!/usr/bin/env python3
"""Termux:API を HTTP に公開する小さなブリッジサーバー。

スマホ本体(Termux)で動かして、同じ tailnet の PC やスマホ自身から curl で叩く。
標準ライブラリのみ・外部依存なし。

    python3 server.py                  # 0.0.0.0:8080
    PORT=9000 python3 server.py
    PHONE_API_TOKEN=s3cret python3 server.py   # トークン必須にする
    python3 server.py --demo           # termux-api 未導入でも骨組みを試す

エンドポイント:
    GET  /health            死活確認(最速)
    GET  /status            バッテリー/Wi-Fi/温度/稼働時間/ストレージ/内部IP の集約
    GET  /battery           termux-battery-status
    GET  /wifi              termux-wifi-connectioninfo
    GET  /sensors           センサー一覧 (termux-sensor -l)
    GET  /sensors?name=accel 特定センサーを1回読む
    GET  /location          termux-location (network)
    GET  /clipboard         termux-clipboard-get
    POST /tts?text=...      termux-tts-speak
    POST /notify?title=&text=
    POST /vibrate?ms=300
    POST /torch?on=1        懐中電灯 on/off
    GET  /photo?cam=0       撮影して JPEG をそのまま返す
    GET  /file?path=...     指定ファイルを返す(ストレージ確認用)
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

VERSION = "1.1.0"
START = time.time()
DEMO = False
TOKEN = os.environ.get("PHONE_API_TOKEN", "")


# ---------------------------------------------------------------- termux 呼び出し
def run(args, timeout=15, binary=False):
    """termux-* を実行。(ok, payload, hint) を返す。"""
    if DEMO:
        return demo(args)
    if shutil.which(args[0]) is None:
        return False, {
            "error": f"{args[0]} が見つからない",
            "hint": "pkg install termux-api  (Termux:API アプリ本体も必要)",
        }, "missing"
    try:
        p = subprocess.run(
            args, capture_output=True, timeout=timeout,
            text=not binary,
        )
    except subprocess.TimeoutExpired:
        return False, {"error": f"{args[0]} が {timeout}s でタイムアウト"}, "timeout"
    if p.returncode != 0:
        err = p.stderr if p.stderr else b""
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return False, {
            "error": f"{args[0]} exit={p.returncode}",
            "stderr": err.strip()[:800],
        }, "exit"
    if binary:
        return True, p.stdout, ""
    out = p.stdout.strip()
    try:
        return True, json.loads(out), ""
    except (json.JSONDecodeError, ValueError):
        return True, out, ""


def file_to_json(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return json.load(f)
    except Exception:
        return None


def demo(args):
    """termux-api が無い環境で配線だけ確認するためのダミーデータ。"""
    cmd = args[0]
    if cmd == "termux-battery-status":
        return True, {"health": "GOOD", "percentage": 82, "plugged": "UNPLUGGED",
                      "status": "DISCHARGING", "temperature": 27.4,
                      "current": -412, "_demo": True}, ""
    if cmd == "termux-wifi-connectioninfo":
        return True, {"ssid": "demo-net", "ip": "100.101.102.103", "link_speed_mbps": 433,
                      "rssi": -51, "frequency_mhz": 5180, "_demo": True}, ""
    if cmd == "termux-sensor":
        if "-l" in args:
            return True, ["accelerometer", "gyroscope", "magnetometer"], ""
        return True, {"accelerometer": {"values": [0.01, 9.79, 0.12]}, "_demo": True}, ""
    if cmd == "termux-location":
        return True, {"latitude": 36.32, "longitude": 139.0, "accuracy": 20.0,
                      "provider": "network", "_demo": True}, ""
    if cmd == "termux-clipboard-get":
        return True, "デモ用クリップボード", ""
    if cmd == "termux-camera-photo":
        # 1x1 PNG
        png = bytes.fromhex(
            "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
            "1f15c4890000000a49444154789c63000100000500010d0a2db4000000"
            "0049454e44ae426082")
        return True, png, ""
    return True, {"ok": True, "_demo": True, "cmd": cmd}, ""


def internal_ip():
    """外向きを掴まずにローカルIPを推定(パケットは飛ばない)。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return None
    finally:
        s.close()


def tailscale_ip():
    if DEMO:
        return "100.101.102.103"
    if shutil.which("tailscale") is None:
        return None
    ok, out, _ = run(["tailscale", "ip", "-4"], timeout=6)
    if ok and isinstance(out, str):
        return out.splitlines()[0].strip() if out else None
    return None


def disk(path="/data/data/com.termux/files/home"):
    try:
        st = os.statvfs(path if os.path.isdir(path) else "/")
    except OSError:
        try:
            st = os.statvfs("/")
        except OSError:
            return None
    free = st.f_bavail * st.f_frsize
    total = st.f_blocks * st.f_frsize
    return {"free_gb": round(free / 1e9, 1), "total_gb": round(total / 1e9, 1),
            "used_pct": round(100 * (1 - free / total), 1) if total else None}


def uptime_s():
    try:
        with open("/proc/uptime") as f:
            return round(float(f.read().split()[0]))
    except Exception:
        return None


def cpu_temp():
    for p in ("/sys/class/thermal/thermal_zone0/temp",
              "/sys/devices/virtual/thermal/thermal_zone0/temp"):
        try:
            with open(p) as f:
                v = int(f.read().strip())
            return round(v / 1000.0, 1) if v > 1000 else round(float(v), 1)
        except Exception:
            continue
    return None


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = f"phoneapi/{VERSION}"
    protocol_version = "HTTP/1.1"

    # -- helpers
    def send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def send_bytes(self, data, ctype="image/jpeg", code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def auth_ok(self, q):
        if not TOKEN:
            return True
        given = self.headers.get("X-Token") or (q.get("token", [""])[0])
        return given == TOKEN

    def log_message(self, fmt, *a):
        sys.stderr.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), fmt % a))

    # -- routes
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path.rstrip("/") or "/"

        if not self.auth_ok(q):
            return self.send_json({"error": "unauthorized",
                                   "hint": "X-Token ヘッダか ?token= を付ける"}, 401)

        if path == "/health":
            return self.send_json({
                "ok": True, "version": VERSION, "demo": DEMO,
                "host": socket.gethostname(), "uptime_s": uptime_s(),
                "termux_api": DEMO or shutil.which("termux-battery-status") is not None,
            })

        if path == "/status":
            ok_b, bat, _ = run(["termux-battery-status"], timeout=10)
            ok_w, wifi, _ = run(["termux-wifi-connectioninfo"], timeout=10)
            if isinstance(wifi, dict) and "error" in wifi:
                ok_w = False
            return self.send_json({
                "ok": True, "demo": DEMO,
                "host": socket.gethostname(),
                "tailscale_ip": tailscale_ip(),
                "internal_ip": internal_ip(),
                "uptime_s": uptime_s(),
                "cpu_temp_c": cpu_temp(),
                "disk": disk(),
                "battery": bat if ok_b else {"error": bat},
                "wifi": (wifi if ok_w else {"error": wifi}),
            })

        if path == "/battery":
            ok, out, _ = run(["termux-battery-status"])
            return self.send_json(out, 200 if ok else 502)

        if path == "/wifi":
            ok, out, _ = run(["termux-wifi-connectioninfo"])
            return self.send_json(out, 200 if ok else 502)

        if path == "/sensors":
            name = q.get("name", [""])[0]
            args = ["termux-sensor", "-s", name, "-n", "1"] if name else ["termux-sensor", "-l"]
            ok, out, _ = run(args, timeout=12)
            return self.send_json(out, 200 if ok else 502)

        if path == "/location":
            ok, out, _ = run(["termux-location", "-p", "network"], timeout=25)
            return self.send_json(out, 200 if ok else 502)

        if path == "/clipboard":
            ok, out, _ = run(["termux-clipboard-get"])
            return self.send_json({"clipboard": out}, 200 if ok else 502)

        if path == "/photo":
            cam = q.get("cam", ["0"])[0]
            dest = os.path.join(os.environ.get("TMPDIR", "/tmp"), f"phone-{int(time.time())}.jpg")
            ok, out, _ = run(["termux-camera-photo", "-c", cam, dest], timeout=20, binary=True)
            if ok and DEMO and isinstance(out, bytes):
                # demo では termux-camera-photo が居ないのでダミー画像を自分で置く
                with open(dest, "wb") as f:
                    f.write(out)
            if not ok or not os.path.exists(dest):
                return self.send_json(out if isinstance(out, dict) else
                                      {"error": "撮影失敗", "hint": "カメラ権限を確認"}, 502)
            with open(dest, "rb") as f:
                data = f.read()
            os.unlink(dest)
            return self.send_bytes(data)

        if path == "/file":
            p = q.get("path", [""])[0]
            if not p or not os.path.isfile(p):
                return self.send_json({"error": "not found", "path": p}, 404)
            with open(p, "rb") as f:
                return self.send_bytes(f.read(), "application/octet-stream")

        return self.send_json({"error": "unknown endpoint", "path": path,
                               "endpoints": ["/health", "/status", "/battery", "/wifi",
                                             "/sensors", "/location", "/clipboard",
                                             "/photo", "/file"]}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path.rstrip("/") or "/"
        if not self.auth_ok(q):
            return self.send_json({"error": "unauthorized"}, 401)

        if path == "/tts":
            text = q.get("text", [""])[0]
            if not text:
                return self.send_json({"error": "text が要る"}, 400)
            ok, out, _ = run(["termux-tts-speak", text], timeout=30)
            return self.send_json({"spoken": text, "ok": ok}, 200 if ok else 502)

        if path == "/notify":
            args = ["termux-notification"]
            if q.get("title"):
                args += ["-t", q["title"][0]]
            if q.get("text"):
                args += ["-c", q["text"][0]]
            ok, out, _ = run(args, timeout=15)
            return self.send_json({"ok": ok}, 200 if ok else 502)

        if path == "/vibrate":
            ms = q.get("ms", ["300"])[0]
            if not ms.isdigit():
                return self.send_json({"error": "ms は数値"}, 400)
            ok, out, _ = run(["termux-vibrate", "-d", ms], timeout=10)
            return self.send_json({"ok": ok, "ms": int(ms)}, 200 if ok else 502)

        if path == "/torch":
            on = q.get("on", ["1"])[0] not in ("0", "false", "off")
            ok, out, _ = run(["termux-torch", "on" if on else "off"], timeout=10)
            return self.send_json({"ok": ok, "torch": "on" if on else "off"}, 200 if ok else 502)

        return self.send_json({"error": "unknown endpoint", "path": path}, 404)


def main():
    global DEMO
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="termux-api 無しで動作確認")
    ap.add_argument("-p", "--port", type=int, default=int(os.environ.get("PORT", "8080")))
    ap.add_argument("--bind", default=os.environ.get("BIND", "0.0.0.0"))
    a = ap.parse_args()
    DEMO = a.demo

    srv = ThreadingHTTPServer((a.bind, a.port), Handler)
    srv.daemon_threads = True
    print(f"phoneapi {VERSION} -> http://{a.bind}:{a.port}  (demo={DEMO})", flush=True)
    if a.bind == "0.0.0.0":
        ips = [f"http://{internal_ip()}:{a.port}"]
        ts = tailscale_ip()
        if ts:
            ips.insert(0, f"http://{ts}:{a.port}")
        print("  試すとき: " + "  ".join(ips), flush=True)
    if TOKEN:
        print("  PHONE_API_TOKEN 有効", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye", flush=True)
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
