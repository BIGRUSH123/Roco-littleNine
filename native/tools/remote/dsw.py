#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""DSW 远端 shell / 文件工具（本地驱动，走 Jupyter Server REST + kernel websocket）

用法:
  python dsw.py ls [path]
  python dsw.py cat <path> [--tail N] [--head N]
  python dsw.py put <local> <remote>
  python dsw.py get <remote> <local>
  python dsw.py rm <path>
  python dsw.py kernels [--restart]
  python dsw.py py <code>
  python dsw.py sh <shell command> [--timeout S]
  python dsw.py bg <shell command> --log <remote log>    # nohup 后台跑
  python dsw.py ping
"""
import argparse
import asyncio
import base64
import json
import os
import sys
import time
import uuid

import requests

HOST = "dsw-gateway-cn-hangzhou.data.aliyun.com"
# 实例号会随重开实例变化：优先读环境变量 DSW_INSTANCE，其次读同目录 instance.txt
_DEFAULT_INSTANCE = "dsw-2203962"
_env = os.environ.get("DSW_INSTANCE", "").strip()
if not _env:
    _f = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance.txt")
    if os.path.exists(_f):
        with open(_f, encoding="utf-8") as fh:
            _env = fh.read().strip()
PREFIX = "/" + (_env or _DEFAULT_INSTANCE)
BASE = f"https://{HOST}{PREFIX}"
HERE = os.path.dirname(os.path.abspath(__file__))
COOKIE_FILE = os.path.join(HERE, "cookies.json")
KERNEL_FILE = os.path.join(HERE, "kernel.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")


def _cookies():
    with open(COOKIE_FILE, encoding="utf-8") as fh:
        jar = json.load(fh)
    pairs, xsrf = [], None
    for c in jar:
        d = c["domain"].lstrip(".")
        if HOST == d or HOST.endswith("." + d):
            pairs.append(f'{c["name"]}={c["value"]}')
            if c["name"] == "_xsrf":
                xsrf = c["value"]
    return "; ".join(pairs), xsrf


def _headers():
    ck, xsrf = _cookies()
    h = {"Cookie": ck, "Origin": BASE, "Referer": BASE + "/lab", "User-Agent": UA}
    if xsrf:
        h["X-XSRFToken"] = xsrf
    return h


def api(method, path, **kw):
    url = f"{BASE}/api/{path.lstrip('/')}"
    r = requests.request(method, url, headers=_headers(), timeout=kw.pop("timeout", 120), **kw)
    if r.status_code >= 400:
        raise SystemExit(f"HTTP {r.status_code} {method} {path}: {r.text[:400]}")
    return r


# ── kernel ──────────────────────────────────────────────────────────
def _load_kernel():
    if os.path.exists(KERNEL_FILE):
        with open(KERNEL_FILE, encoding="utf-8") as fh:
            return json.load(fh).get("id")
    return None


def _save_kernel(kid):
    with open(KERNEL_FILE, "w", encoding="utf-8") as fh:
        json.dump({"id": kid}, fh)


def get_kernel(restart=False):
    kid = None if restart else _load_kernel()
    live = {k["id"] for k in api("GET", "kernels").json()}
    if kid and kid in live:
        return kid
    if restart and kid:
        try:
            api("DELETE", f"kernels/{kid}")
        except SystemExit:
            pass
    r = api("POST", "kernels", json={"name": "python3"})
    kid = r.json()["id"]
    _save_kernel(kid)
    time.sleep(2.0)  # 等 kernel 起来
    return kid


def _msg(kid_session, msg_type, content, channel="shell"):
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000000Z", time.gmtime())
    return {
        "header": {"msg_id": uuid.uuid4().hex, "username": "dsw", "session": kid_session,
                   "msg_type": msg_type, "version": "5.3", "date": now},
        "parent_header": {}, "metadata": {}, "content": content,
        "channel": channel, "buffers": [],
    }


async def _exec(kid, code, timeout, silent=False):
    import websockets

    url = f"wss://{HOST}{PREFIX}/api/kernels/{kid}/channels"
    ck, _ = _cookies()
    hdrs = {"Cookie": ck, "Origin": BASE, "User-Agent": UA}
    try:
        conn = websockets.connect(url, additional_headers=hdrs, max_size=None,
                                  open_timeout=30, ping_interval=None)
    except TypeError:  # websockets < 12
        conn = websockets.connect(url, extra_headers=hdrs, max_size=None,
                                  open_timeout=30, ping_interval=None)
    async with conn as ws:
        sess = uuid.uuid4().hex
        req = _msg(sess, "execute_request",
                   {"code": code, "silent": silent, "store_history": False,
                    "user_expressions": {}, "allow_stdin": False, "stop_on_error": True})
        await ws.send(json.dumps(req))
        chunks, errors, status = [], [], None
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                return "".join(chunks) + f"\n[TIMEOUT after {timeout}s]", False
            raw = await asyncio.wait_for(ws.recv(), timeout=left)
            m = json.loads(raw)
            mt = m.get("header", {}).get("msg_type")
            c = m.get("content", {}) or {}
            if mt == "stream":
                chunks.append(c.get("text", ""))
            elif mt == "error":
                errors.append("\n".join(c.get("traceback", [])) or c.get("evalue", ""))
            elif mt == "execute_result":
                chunks.append(str(c.get("data", {}).get("text/plain", "")))
            elif mt == "execute_reply":
                status = c.get("status")
                break
        text = "".join(chunks)
        if errors:
            text += "\n" + "\n".join(errors)
        return text, status == "ok"


def run_code(code, timeout=600, restart=False):
    kid = get_kernel(restart)
    try:
        return asyncio.run(_exec(kid, code, timeout))
    except Exception as exc:  # kernel 可能死了 → 重建一次
        kid = get_kernel(restart=True)
        return asyncio.run(_exec(kid, code, timeout))


def shell(cmd, timeout=600):
    code = ("import subprocess as _sp, sys as _s\n"
            f"_cmd = {json.dumps(cmd)}\n"
            "_p = _sp.run(_cmd, shell=True, capture_output=True, text=True)\n"
            "_s.stdout.write(_p.stdout or '')\n"
            "_s.stderr.write(_p.stderr or '')\n"
            "print('__EXIT__', _p.returncode)\n")
    out, ok = run_code(code, timeout=timeout)
    return out, ok


def background(cmd, log):
    code = ("import subprocess as _sp\n"
            f"_cmd = {json.dumps(cmd)}\n"
            f"_log = {json.dumps(log)}\n"
            "_f = open(_log, 'ab')\n"
            "_p = _sp.Popen(['bash', '-lc', _cmd], stdout=_f, stderr=_sp.STDOUT,\n"
            "               stdin=_sp.DEVNULL, start_new_session=True)\n"
            "print('__PID__', _p.pid)\n")
    return run_code(code, timeout=120)


# ── CLI ─────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("ls"); p.add_argument("path", nargs="?", default="")
    p = sub.add_parser("cat"); p.add_argument("path")
    p.add_argument("--tail", type=int, default=0); p.add_argument("--head", type=int, default=0)
    p = sub.add_parser("put"); p.add_argument("local"); p.add_argument("remote")
    p = sub.add_parser("get"); p.add_argument("remote"); p.add_argument("local")
    p = sub.add_parser("rm"); p.add_argument("path")
    p = sub.add_parser("kernels"); p.add_argument("--restart", action="store_true")
    p = sub.add_parser("py"); p.add_argument("code")
    p = sub.add_parser("sh"); p.add_argument("command"); p.add_argument("--timeout", type=int, default=600)
    p = sub.add_parser("bg"); p.add_argument("command"); p.add_argument("--log", required=True)
    sub.add_parser("ping")

    a = ap.parse_args()

    if a.cmd == "ls":
        data = api("GET", "contents/" + a.path.lstrip("/"), params={"content": 1}).json()
        items = data.get("content") or ([] if data.get("type") == "directory" else [data])
        for it in items:
            print(f'{it["type"][:4]}\t{(it.get("size") or 0):>12}\t{it["name"]}')
    elif a.cmd == "cat":
        data = api("GET", "contents/" + a.path.lstrip("/"), params={"content": 1}).json()
        body = data.get("content", "")
        lines = body.splitlines()
        if a.tail:
            lines = lines[-a.tail:]
        if a.head:
            lines = lines[:a.head]
        print("\n".join(lines))
    elif a.cmd == "put":
        with open(a.local, "rb") as fh:
            raw = fh.read()
        api("PUT", "contents/" + a.remote.lstrip("/"),
            json={"type": "file", "format": "base64",
                  "content": base64.b64encode(raw).decode()})
        print(f"uploaded {a.local} -> {a.remote} ({len(raw)} B)")
    elif a.cmd == "get":
        data = api("GET", "contents/" + a.remote.lstrip("/"),
                   params={"content": 1, "format": "base64"}).json()
        raw = base64.b64decode(data["content"]) if data.get("format") == "base64" \
            else data["content"].encode()
        with open(a.local, "wb") as fh:
            fh.write(raw)
        print(f"downloaded {a.remote} -> {a.local} ({len(raw)} B)")
    elif a.cmd == "rm":
        api("DELETE", "contents/" + a.path.lstrip("/"))
        print(f"deleted {a.path}")
    elif a.cmd == "kernels":
        ks = api("GET", "kernels").json()
        print(json.dumps([{"id": k["id"], "state": k.get("execution_state"),
                           "last": k.get("last_activity")} for k in ks], indent=1))
        if a.restart:
            kid = get_kernel(restart=True)
            print("new kernel:", kid)
    elif a.cmd == "ping":
        kid = get_kernel()
        out, ok = run_code("import sys, socket; print(sys.version.split()[0], socket.gethostname())",
                           timeout=120)
        print(out.strip(), "| kernel:", kid, "| ok:", ok)
    elif a.cmd == "py":
        out, ok = run_code(a.code)
        print(out)
        sys.exit(0 if ok else 1)
    elif a.cmd == "sh":
        out, ok = shell(a.command, timeout=a.timeout)
        print(out)
        sys.exit(0 if ok else 1)
    elif a.cmd == "bg":
        out, _ = background(a.command, a.log)
        print(out)


if __name__ == "__main__":
    main()
