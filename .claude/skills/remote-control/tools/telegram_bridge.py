#!/usr/bin/env python3
"""Telegram <-> Claude Code bridge daemon.

Long-polls Telegram. Each text message from an allowed chat is handed to a
headless `claude -p` run in the repo root, so the FULL agent (every skill) is
reachable from your phone. The agent's risky actions are gated by the Telegram
approval hook (telegram_approve.py) — the daemon keeps polling while an agent
runs, so it can deliver the inline button press back to the waiting hook.

Run it on the always-on machine (your Windows box):
    python telegram_bridge.py            # start the daemon
    python telegram_bridge.py --test     # ping allowed chats and exit

Stop with Ctrl+C.

Commands inside Telegram:
    /help          show help
    /hosts         list SSH hosts from work/hosts.json
    /reset         drop the conversation, start fresh next message
    /whoami        show your chat id (to confirm allowlisting)
"""

import json
import os
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import approvals  # noqa: E402
import rc_config as cfg  # noqa: E402
import ssh_exec  # noqa: E402
import tg_api  # noqa: E402

HOOK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "telegram_approve.py")
SESSIONS_FILE = os.path.join(cfg.temp_dir(), "..", "tg_sessions.json")
_busy = set()           # chat ids with a running agent
_busy_lock = threading.Lock()


# ── session continuity ────────────────────────────────────────────
def _load_sessions() -> dict:
    try:
        with open(SESSIONS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_session(chat, sid):
    data = _load_sessions()
    if sid:
        data[str(chat)] = sid
    else:
        data.pop(str(chat), None)
    with open(SESSIONS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


# ── bridge settings file (attaches the approval hook to spawned agents) ──
def _write_bridge_settings() -> str:
    path = os.path.join(cfg.temp_dir(), "..", "bridge_settings.json")
    cmd = f'"{sys.executable}" "{HOOK_PATH}"'
    settings = {"hooks": {"PreToolUse": [
        {"matcher": "*", "hooks": [{"type": "command", "command": cmd}]}
    ]}}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)
    return path


def _claude_bin() -> str:
    return cfg.get("CLAUDE_BIN") or "claude"


# ── agent execution ───────────────────────────────────────────────
def _run_agent(chat, text, settings_path):
    """Run one `claude -p` turn for this chat and send the result back."""
    tg_api.send_message(chat, "⏳ <i>Agent đang xử lý…</i>")
    argv = [_claude_bin(), "-p", text,
            "--output-format", "json",
            "--settings", settings_path]
    sessions = _load_sessions()
    sid = sessions.get(str(chat))
    if sid:
        argv += ["--resume", sid]
    if cfg.get("TELEGRAM_AGENT_MODEL"):
        argv += ["--model", cfg.get("TELEGRAM_AGENT_MODEL")]

    env = dict(os.environ)
    env["CLAUDE_TG_BRIDGE"] = "1"
    env["CLAUDE_TG_CHAT_ID"] = str(chat)

    try:
        proc = subprocess.run(
            argv, cwd=cfg.repo_root(), env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=int(cfg.get("TELEGRAM_AGENT_TIMEOUT", "1800") or "1800"))
    except FileNotFoundError:
        tg_api.send_message(chat, "❌ Không tìm thấy <code>claude</code> trên PATH "
                            "(đặt CLAUDE_BIN trong .env nếu cần).")
        return
    except subprocess.TimeoutExpired:
        tg_api.send_message(chat, "⏱️ Agent chạy quá lâu, đã hủy.")
        return
    finally:
        with _busy_lock:
            _busy.discard(str(chat))

    out = proc.stdout.strip()
    result_text, new_sid = out, None
    try:
        obj = json.loads(out)
        result_text = obj.get("result") or obj.get("error") or out
        new_sid = obj.get("session_id")
        if obj.get("is_error"):
            result_text = "⚠️ " + str(result_text)
    except ValueError:
        if proc.stderr.strip():
            result_text = (out + "\n" + proc.stderr).strip() or proc.stderr.strip()
    if new_sid:
        _save_session(chat, new_sid)
    tg_api.send_message(chat, result_text or "(agent không trả về nội dung)")


# ── command + update handling ─────────────────────────────────────
def _handle_command(chat, text) -> bool:
    cmd = text.strip().split()[0].lower()
    if cmd in ("/start", "/help"):
        tg_api.send_message(chat,
            "🤖 <b>Remote-control bridge</b>\n"
            "Nhắn yêu cầu thường (vd <i>“review MR 123”</i>, <i>“task của tôi”</i>, "
            "<i>“ssh may-build chạy df -h”</i>) → agent xử lý.\n"
            "Thao tác ghi/SSH/nguy hiểm sẽ hỏi duyệt bằng nút bấm.\n\n"
            "<b>Lệnh:</b> /hosts /reset /whoami")
        return True
    if cmd == "/whoami":
        tg_api.send_message(chat, f"chat_id của bạn: <code>{chat}</code>")
        return True
    if cmd == "/reset":
        _save_session(chat, None)
        tg_api.send_message(chat, "🧹 Đã xóa ngữ cảnh. Tin nhắn tới sẽ bắt đầu phiên mới.")
        return True
    if cmd == "/hosts":
        hosts = ssh_exec.load_hosts()
        if not hosts:
            tg_api.send_message(chat, "Chưa có host nào trong <code>work/hosts.json</code>.")
        else:
            lines = [f"• <code>{a}</code> → {h.get('user','')}@{h.get('host')}"
                     for a, h in hosts.items()]
            tg_api.send_message(chat, "<b>SSH hosts:</b>\n" + "\n".join(lines))
        return True
    return False


def _handle_callback(cbq):
    data = cbq.get("data", "")
    cb_id = cbq.get("id")
    if not data.startswith("appr:"):
        tg_api.answer_callback(cb_id)
        return
    _, req_id, verdict = data.split(":", 2)
    approved = verdict == "yes"
    rec = approvals.decide(req_id, approved, by=str(cbq.get("from", {}).get("id", "")))
    msg = cbq.get("message", {})
    label = "✅ ĐÃ DUYỆT" if approved else "❌ ĐÃ TỪ CHỐI"
    if rec:
        tg_api.edit_message_text(msg.get("chat", {}).get("id"), msg.get("message_id"),
                                 f"{label}\n<code>{rec.get('summary','')}</code>")
    tg_api.answer_callback(cb_id, label)


def _handle_message(msg, settings_path):
    chat = msg.get("chat", {}).get("id")
    text = msg.get("text", "")
    if not text:
        return
    if not tg_api.is_allowed(chat):
        tg_api.send_message(chat, "⛔ Chat chưa được cấp quyền. "
                            f"chat_id: <code>{chat}</code> — thêm vào TELEGRAM_ALLOWED_CHATS.")
        return
    if text.startswith("/") and _handle_command(chat, text):
        return
    with _busy_lock:
        if str(chat) in _busy:
            tg_api.send_message(chat, "⏳ Đang chạy một yêu cầu, đợi xong rồi nhắn tiếp nhé.")
            return
        _busy.add(str(chat))
    threading.Thread(target=_run_agent, args=(chat, text, settings_path),
                     daemon=True).start()


def serve():
    missing = [k for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_CHATS") if not cfg.get(k)]
    if missing:
        print(f"Thiếu config: {', '.join(missing)} (đặt trong .env)")
        sys.exit(1)
    settings_path = _write_bridge_settings()
    print(f"Bridge khởi động. Hub repo: {cfg.repo_root()}")
    print(f"Allowed chats: {tg_api.allowed_chats()}")
    print("Ctrl+C để dừng.")
    offset = 0
    while True:
        try:
            resp = tg_api.get_updates(offset, timeout=30)
            if not resp.get("ok"):
                time.sleep(3)
                continue
            for upd in resp.get("result", []):
                offset = upd["update_id"] + 1
                if "callback_query" in upd:
                    _handle_callback(upd["callback_query"])
                elif "message" in upd:
                    _handle_message(upd["message"], settings_path)
        except KeyboardInterrupt:
            print("\nDừng bridge.")
            break
        except Exception as e:  # noqa: BLE001 - keep the daemon alive
            print(f"[bridge] lỗi vòng lặp: {e}")
            time.sleep(3)


if __name__ == "__main__":
    if "--test" in sys.argv:
        for c in tg_api.allowed_chats():
            r = tg_api.send_message(c, "✅ Bridge test OK.")
            print(f"{c}: {'ok' if r.get('ok') else r.get('description')}")
        sys.exit(0)
    serve()
