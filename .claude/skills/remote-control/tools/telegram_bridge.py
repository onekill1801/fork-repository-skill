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
import choices  # noqa: E402
import md2tg  # noqa: E402
import rc_config as cfg  # noqa: E402
import ssh_exec  # noqa: E402
import tg_api  # noqa: E402

HOOK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "telegram_approve.py")

# Taught to every bridge-spawned agent so plan/decision prompts arrive as
# tappable Telegram buttons instead of unselectable prose. The bridge detects
# this block in the reply (choices.parse) and renders an inline keyboard.
CHOICE_SYS = (
    "Bạn đang trả lời qua Telegram (không có giao diện chọn tương tác). "
    "KHI VÀ CHỈ KHI bạn cần người dùng chọn giữa các phương án (ví dụ chọn hướng "
    "plan, xác nhận một lựa chọn), ĐỪNG hỏi bằng văn xuôi. Thay vào đó kết thúc "
    "câu trả lời bằng đúng một khối theo định dạng:\n"
    "[[TG_CHOICE]]\n"
    "question: <câu hỏi ngắn gọn>\n"
    "1. <phương án 1>\n"
    "2. <phương án 2>\n"
    "[[/TG_CHOICE]]\n"
    "Mỗi phương án một dòng, tối đa 8 phương án, mỗi phương án dưới 60 ký tự để "
    "hiển thị làm nút bấm. Không dùng khối này nếu không thực sự cần người dùng quyết định."
)
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
def _is_stale_session(text: str) -> bool:
    """True if `claude --resume` failed because the session no longer exists."""
    t = (text or "").lower()
    return "no conversation found" in t or "session id" in t and "not found" in t


def _invoke_claude(chat, text, settings_path, sid):
    """Run one `claude -p` call. Returns (result_text, new_sid, is_error) or
    None if the binary is missing / it timed out (already reported to the user)."""
    argv = [_claude_bin(), "-p", text,
            "--output-format", "json",
            "--append-system-prompt", CHOICE_SYS,
            "--settings", settings_path]
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
        return None
    except subprocess.TimeoutExpired:
        tg_api.send_message(chat, "⏱️ Agent chạy quá lâu, đã hủy.")
        return None

    out = proc.stdout.strip()
    result_text, new_sid, is_error = out, None, False
    try:
        obj = json.loads(out)
        result_text = obj.get("result") or obj.get("error") or out
        new_sid = obj.get("session_id")
        is_error = bool(obj.get("is_error"))
    except ValueError:
        if proc.stderr.strip():
            result_text = (out + "\n" + proc.stderr).strip() or proc.stderr.strip()
        if proc.returncode != 0:
            is_error = True
    return result_text, new_sid, is_error


def _run_agent(chat, text, settings_path):
    """Run one turn for this chat and send the result back. If a saved session
    has gone stale, drop it and retry once with a fresh conversation."""
    tg_api.send_message(chat, "⏳ <i>Agent đang xử lý…</i>")
    try:
        sid = _load_sessions().get(str(chat))
        res = _invoke_claude(chat, text, settings_path, sid)
        if res is None:
            return
        result_text, new_sid, is_error = res
        if is_error and sid and _is_stale_session(result_text):
            _save_session(chat, None)
            tg_api.send_message(chat, "♻️ Phiên cũ không còn, bắt đầu phiên mới…")
            res = _invoke_claude(chat, text, settings_path, None)
            if res is None:
                return
            result_text, new_sid, is_error = res
        if is_error:
            result_text = "⚠️ " + str(result_text)
        if new_sid:
            _save_session(chat, new_sid)
        _deliver(chat, result_text or "(agent không trả về nội dung)")
    finally:
        with _busy_lock:
            _busy.discard(str(chat))


def _deliver(chat, result_text):
    """Send the agent's reply. If it ends with a TG_CHOICE block, render the
    options as tappable inline buttons instead of plain (unselectable) text.

    The plan/reasoning (preamble) and the choice card are sent as SEPARATE
    messages: a long plan auto-chunks on its own, and the choice card lists
    every option as full numbered text so nothing is hidden behind a truncated
    button label."""
    parsed = choices.parse(result_text)
    if not parsed:
        tg_api.send_message(chat, md2tg.to_html(result_text))
        return
    preamble, question, options = parsed
    token = choices.create(chat, question, options)
    if preamble:
        tg_api.send_message(chat, md2tg.to_html(preamble))  # full plan, chunked if long
    lines = [f"❓ <b>{md2tg._inline(question)}</b>", ""]
    lines += [f"<b>{i + 1}.</b> {md2tg._inline(opt)}" for i, opt in enumerate(options)]
    lines += ["", "<i>Bấm nút bên dưới để chọn.</i>"]
    tg_api.send_message(chat, "\n".join(lines),
                        reply_markup=tg_api.choices_keyboard(token, options))


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


def _handle_callback(cbq, settings_path):
    data = cbq.get("data", "")
    cb_id = cbq.get("id")
    if data.startswith("appr:"):
        _handle_approval_cb(cbq, data, cb_id)
    elif data.startswith("choice:"):
        _handle_choice_cb(cbq, data, cb_id, settings_path)
    else:
        tg_api.answer_callback(cb_id)


def _handle_approval_cb(cbq, data, cb_id):
    _, req_id, verdict = data.split(":", 2)
    approved = verdict == "yes"
    rec = approvals.decide(req_id, approved, by=str(cbq.get("from", {}).get("id", "")))
    msg = cbq.get("message", {})
    label = "✅ ĐÃ DUYỆT" if approved else "❌ ĐÃ TỪ CHỐI"
    if rec:
        tg_api.edit_message_text(msg.get("chat", {}).get("id"), msg.get("message_id"),
                                 f"{label}\n<code>{rec.get('summary','')}</code>")
    tg_api.answer_callback(cb_id, label)


def _handle_choice_cb(cbq, data, cb_id, settings_path):
    import html
    _, token, idx = data.split(":", 2)
    chosen = choices.resolve(token, int(idx))
    msg = cbq.get("message", {})
    if not chosen:
        tg_api.answer_callback(cb_id, "Lựa chọn đã hết hạn.")
        return
    rec = choices.get(token)
    chat = rec.get("chat") or msg.get("chat", {}).get("id")
    tg_api.edit_message_text(
        msg.get("chat", {}).get("id"), msg.get("message_id"),
        f"❓ <b>{html.escape(rec.get('question', ''))}</b>\n"
        f"➡️ <b>Đã chọn:</b> <code>{html.escape(chosen)}</code>")
    tg_api.answer_callback(cb_id, f"Đã chọn: {chosen[:60]}")
    # Resume the agent with the picked option as the next turn.
    _start_turn(chat, f"Tôi chọn phương án: {chosen}", settings_path)


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
    _start_turn(chat, text, settings_path)


def _start_turn(chat, text, settings_path) -> bool:
    """Spawn one agent turn for `chat` unless one is already running. Returns
    False (and notifies) if the chat is busy."""
    with _busy_lock:
        if str(chat) in _busy:
            tg_api.send_message(chat, "⏳ Đang chạy một yêu cầu, đợi xong rồi nhắn tiếp nhé.")
            return False
        _busy.add(str(chat))
    threading.Thread(target=_run_agent, args=(chat, text, settings_path),
                     daemon=True).start()
    return True


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
                    _handle_callback(upd["callback_query"], settings_path)
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
