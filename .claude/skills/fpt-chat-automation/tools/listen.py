#!/usr/bin/env python3
"""Listen for incoming FPT Chat messages over the SocketCluster realtime socket.

Behaviour:
  - DIRECT (1-1) messages from someone else  -> spawn a Claude Code terminal
    (your Pro subscription, no API key) to draft & send a reply via send.py.
  - GROUP messages                           -> log only, never auto-reply.
  - your own messages / typing / seen / etc. -> ignored.

Transport (verified): wss://realtime-chat.fpt.com/realtime, JWT in
Sec-WebSocket-Protocol. Incoming items arrive on topic user_<myId> as
{"type":"message","data":{... ,"type":"TEXT","content","senderId",
"group":{"isDirectChat":bool,"name":...}, "messageIdInc", ...}}.

Heartbeat 'ping' keeps the socket alive; auto-reconnect + token refresh on drop.
Runs until Ctrl+C.

Usage:
  python listen.py                       # reply mode = claude (default)
  python listen.py --reply notify        # just print DMs, no auto-reply
  python listen.py --reply off           # log everything, take no action
  python listen.py --cooldown 30         # min seconds between claude spawns per chat
"""

import argparse
import json
import os
import re
import socket
import sys
import time

import client
import config
import tokens
import ws_client

# import the existing fork-terminal tool (cross-platform terminal spawner)
_FT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "fork-terminal", "tools"))
sys.path.insert(0, _FT_DIR)
try:
    import fork_terminal  # noqa: E402
except Exception:
    fork_terminal = None

# context files for forked Claude sessions live in gitignored temp/
_INBOX = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..",
                                      "temp", "fchat_incoming"))


def _now():
    return time.strftime("%H:%M:%S")


def _looks_encrypted(text) -> bool:
    if not isinstance(text, str) or len(text) < 40 or " " in text:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9+/=]+", text))


def _write_context(data, sender_name, encrypted) -> str:
    os.makedirs(_INBOX, exist_ok=True)
    gid = data.get("groupId")
    inc = data.get("messageIdInc")
    group = data.get("group") or {}
    gtype = group.get("type") or group.get("groupType") or "SUPER_PRIVATE"
    path = os.path.join(_INBOX, f"dm_{gid}_{inc}.md")
    content = data.get("content")
    body = f"""# Tin nhắn riêng (DM) trên FPT Chat cần trả lời

- Người gửi: **{sender_name}** (`{data.get('senderId')}`)
- groupId: `{gid}`
- groupType: `{gtype}`
- messageIdInc: {inc}
- Thời gian: {data.get('createdAt')}
- Mã hoá E2E: {"CÓ — nội dung dưới là ciphertext, KHÔNG đọc được" if encrypted else "không"}

## Nội dung
```
{content}
```

## Việc cần làm
1. Đọc nội dung trên. {"Vì tin bị mã hoá E2E, hãy báo người dùng là không đọc được và DỪNG (đừng gửi gì)." if encrypted else "Soạn một câu trả lời phù hợp, lịch sự, đúng ngữ cảnh."}
2. Gửi trả lời bằng skill fpt-chat-automation:
   ```
   cd .claude/skills/fpt-chat-automation/tools
   python send.py text --group {gid} --content "<câu trả lời>" --group-type {gtype} --yes
   ```
3. Hỏi xác nhận của người dùng trước khi chạy lệnh có `--yes` (đây là tin gửi cho người thật).
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    return path


def _spawn_claude(ctx_path):
    if fork_terminal is None:
        print(f"[{_now()}] [WARN] fork-terminal not available; context written to {ctx_path}",
              file=sys.stderr)
        return
    # minimal ASCII command -> avoids quoting/encoding pitfalls; instructions live in the file
    cmd = f'claude "Read and follow the reply instructions in {ctx_path}"'
    try:
        fork_terminal.fork_terminal(cmd)
        print(f"[{_now()}] -> spawned Claude terminal to reply ({ctx_path})")
    except Exception as e:
        print(f"[{_now()}] [WARN] could not spawn terminal: {e}; context at {ctx_path}",
              file=sys.stderr)


def handle_message(obj, me, reply_mode, cooldown, seen, last_spawn):
    data = obj.get("data") or {}
    if data.get("type") != "TEXT":
        return
    sender = data.get("senderId")
    if not sender or sender == me:
        return  # ignore own messages (prevents reply loops)
    gid = data.get("groupId")
    inc = data.get("messageIdInc")
    key = (gid, inc)
    if key in seen:
        return
    seen.add(key)

    group = data.get("group") or {}
    is_direct = group.get("isDirectChat")
    user = data.get("user") or {}
    sender_name = user.get("displayName") or sender
    gname = group.get("name") or gid
    content = data.get("content")

    if not is_direct:
        print(f"[{_now()}] [GROUP] {gname} | {sender_name}: {content!r}  (chỉ đọc)")
        return

    enc = _looks_encrypted(content)
    shown = "<E2E ciphertext>" if enc else repr(content)
    print(f"[{_now()}] [DM]    {sender_name}: {shown}")

    if reply_mode == "off":
        return
    if reply_mode == "notify":
        return
    # reply_mode == "claude"
    now = time.time()
    if cooldown and (now - last_spawn.get(gid, 0)) < cooldown:
        print(f"[{_now()}]        (trong cooldown {cooldown}s — bỏ qua spawn, đã log)")
        return
    last_spawn[gid] = now
    ctx = _write_context(data, sender_name, enc)
    _spawn_claude(ctx)


def run(reply_mode, cooldown, idle_ping=20):
    me = client.api_get("/user/me").get("id")
    if not me:
        print("[ERROR] cannot resolve current user (token invalid?)", file=sys.stderr)
        sys.exit(1)
    print(f"[{_now()}] listening as {me} | reply={reply_mode} | Ctrl+C to stop")
    seen, last_spawn = set(), {}
    backoff = 2

    while True:
        try:
            ws = ws_client.WebSocket(config.ws_url(), subprotocols=[tokens.ensure_fresh()],
                                     origin="https://chat.fpt.com", timeout=idle_ping,
                                     verify=config.verify_ssl())
            print(f"[{_now()}] connected.")
            backoff = 2
            last_ping = time.time()
            while True:
                try:
                    msg = ws.recv()
                except (socket.timeout, TimeoutError):
                    ws.send_text("ping")          # heartbeat on idle
                    last_ping = time.time()
                    continue
                if msg is None:
                    print(f"[{_now()}] socket closed; reconnecting...")
                    break
                if msg == "pong" or msg == "ping":
                    continue
                try:
                    obj = json.loads(msg) if isinstance(msg, str) else None
                except Exception:
                    obj = None
                if isinstance(obj, dict) and obj.get("type") == "message":
                    handle_message(obj, me, reply_mode, cooldown, seen, last_spawn)
                if time.time() - last_ping > idle_ping:
                    ws.send_text("ping")
                    last_ping = time.time()
            ws.close()
        except KeyboardInterrupt:
            print(f"\n[{_now()}] stopped.")
            return
        except Exception as e:
            print(f"[{_now()}] connection error: {e}; retry in {backoff}s", file=sys.stderr)
            # token may have expired — refresh proactively before retry
            tokens.refresh(verbose=False)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    missing = config.validate()
    if missing:
        print(f"[ERROR] Missing config: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    p = argparse.ArgumentParser(prog="listen.py")
    p.add_argument("--reply", choices=["claude", "notify", "off"], default="claude")
    p.add_argument("--cooldown", type=int, default=30,
                   help="min seconds between Claude spawns per conversation")
    a = p.parse_args()
    try:
        run(a.reply, a.cooldown)
    except KeyboardInterrupt:
        print("\nstopped.")
