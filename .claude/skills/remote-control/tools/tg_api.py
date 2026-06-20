#!/usr/bin/env python3
"""Telegram Bot API client (stdlib urllib only).

Reads TELEGRAM_BOT_TOKEN / TELEGRAM_ALLOWED_CHATS from .env (via rc_config).
Used by telegram_bridge.py (the daemon) and telegram_approve.py (the hook).

CLI (handy for testing the token/chat wiring):
    python tg_api.py me                       # getMe -> bot identity
    python tg_api.py updates                   # show chat_id of anyone who messaged the bot
    python tg_api.py send <chat_id> "text"     # send a message
    python tg_api.py test                      # ping every allowed chat
"""

import json
import os
import ssl
import sys
import urllib.error
import urllib.request

import rc_config as cfg

API = "https://api.telegram.org"
MAX_LEN = 4000  # Telegram hard limit is 4096; leave headroom for HTML tags.

_SSL_WARNED = False


def _verify_ssl() -> bool:
    """TELEGRAM_VERIFY_SSL wins; falls back to the repo-wide SSL_VERIFY. Default true."""
    val = cfg.get("TELEGRAM_VERIFY_SSL") or cfg.get("SSL_VERIFY") or "true"
    return val.strip().lower() not in ("false", "0", "no", "off")


def _ssl_ctx():
    """Build an SSL context. Honors a corporate CA bundle, or disables verify
    (with a one-time warning) on networks that do TLS inspection."""
    global _SSL_WARNED
    ca = cfg.get("TELEGRAM_CA_BUNDLE") or cfg.get("CA_BUNDLE")
    if _verify_ssl():
        if ca and os.path.isfile(ca):
            return ssl.create_default_context(cafile=ca)
        return ssl.create_default_context()
    if not _SSL_WARNED:
        sys.stderr.write("⚠️  TELEGRAM_VERIFY_SSL=false — bỏ qua xác thực chứng chỉ TLS "
                         "tới api.telegram.org (chấp nhận do proxy SSL nội bộ).\n")
        _SSL_WARNED = True
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _token() -> str:
    tok = cfg.get("TELEGRAM_BOT_TOKEN")
    if not tok:
        raise RuntimeError("TELEGRAM_BOT_TOKEN missing in .env")
    return tok


def allowed_chats() -> list:
    return cfg.get_list("TELEGRAM_ALLOWED_CHATS")


def is_allowed(chat_id) -> bool:
    allow = allowed_chats()
    if not allow:
        return False  # fail closed: no allowlist => nobody is authorized
    return str(chat_id) in allow


def call(method: str, params: dict, timeout: int = 35) -> dict:
    """POST a Bot API method with a JSON body. Returns the parsed response."""
    url = f"{API}/bot{_token()}/{method}"
    data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except ValueError:
            return {"ok": False, "error_code": e.code, "description": body}
    except Exception as e:  # noqa: BLE001 - surface as structured error
        return {"ok": False, "description": str(e)}


def _chunks(text: str):
    while text:
        yield text[:MAX_LEN]
        text = text[MAX_LEN:]


def send_message(chat_id, text: str, reply_markup=None,
                 parse_mode: str = "HTML", disable_preview: bool = True) -> dict:
    """Send text (auto-chunked). Markup is only attached to the last chunk."""
    text = text if text.strip() else "(trống)"
    parts = list(_chunks(text)) or [""]
    last = {}
    for i, part in enumerate(parts):
        params = {
            "chat_id": chat_id,
            "text": part,
            "disable_web_page_preview": disable_preview,
        }
        if parse_mode:
            params["parse_mode"] = parse_mode
        if reply_markup is not None and i == len(parts) - 1:
            params["reply_markup"] = reply_markup
        last = call("sendMessage", params)
        # If HTML parse fails (unbalanced tags from agent output), retry as plain.
        if not last.get("ok") and parse_mode:
            params.pop("parse_mode", None)
            last = call("sendMessage", params)
    return last


def get_updates(offset: int, timeout: int = 30) -> dict:
    return call("getUpdates", {
        "offset": offset,
        "timeout": timeout,
        "allowed_updates": ["message", "callback_query"],
    }, timeout=timeout + 10)


def answer_callback(callback_query_id: str, text: str = "") -> dict:
    return call("answerCallbackQuery",
                {"callback_query_id": callback_query_id, "text": text})


def edit_message_text(chat_id, message_id, text: str,
                      parse_mode: str = "HTML") -> dict:
    return call("editMessageText", {
        "chat_id": chat_id, "message_id": message_id,
        "text": text[:MAX_LEN], "parse_mode": parse_mode,
    })


def approve_keyboard(req_id: str) -> dict:
    return {"inline_keyboard": [[
        {"text": "✅ Duyệt", "callback_data": f"appr:{req_id}:yes"},
        {"text": "❌ Từ chối", "callback_data": f"appr:{req_id}:no"},
    ]]}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1].lower()
    if cmd == "me":
        print(json.dumps(call("getMe", {}), indent=2, ensure_ascii=False))
    elif cmd == "send" and len(sys.argv) >= 4:
        print(json.dumps(send_message(sys.argv[2], sys.argv[3]),
                         indent=2, ensure_ascii=False))
    elif cmd == "updates":
        resp = call("getUpdates", {"timeout": 0})
        seen = {}
        for upd in resp.get("result", []):
            msg = upd.get("message") or upd.get("callback_query", {}).get("message", {})
            chat = msg.get("chat", {})
            if chat.get("id") is not None:
                seen[chat["id"]] = chat.get("username") or chat.get("title") \
                    or chat.get("first_name", "")
        if not seen:
            print("Chưa thấy update nào. Hãy NHẮN một tin cho bot trước, rồi chạy lại.")
        for cid, name in seen.items():
            print(f"chat_id = {cid}   ({name})")
    elif cmd == "test":
        chats = allowed_chats()
        if not chats:
            print("TELEGRAM_ALLOWED_CHATS is empty — set it in .env first.")
            sys.exit(1)
        for c in chats:
            r = send_message(c, "✅ <b>remote-control</b> bridge: kết nối OK.")
            print(f"{c}: {'ok' if r.get('ok') else r.get('description')}")
    else:
        print(__doc__)
        sys.exit(1)
