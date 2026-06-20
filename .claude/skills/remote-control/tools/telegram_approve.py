#!/usr/bin/env python3
"""PreToolUse hook: gate risky tool calls through a Telegram approve/deny button.

ONLY active when the spawning process sets CLAUDE_TG_BRIDGE=1 (the bridge does
this for agents it launches). In any other context the hook prints nothing and
exits 0, so your interactive Claude Code sessions are unaffected.

Claude Code hook protocol:
  stdin  : JSON {tool_name, tool_input, session_id, ...}
  stdout : JSON {"hookSpecificOutput": {"hookEventName":"PreToolUse",
                 "permissionDecision":"allow|deny", "permissionDecisionReason":...}}

Decision policy in bridge mode:
  - read-only tools / read-only Bash    -> allow (no ping)
  - Edit/Write/MultiEdit/NotebookEdit   -> ask via Telegram
  - Bash 'write' or 'danger'            -> ask (danger is flagged ⚠️)
  - anything unrecognized               -> ask (fail safe)
  - Telegram unreachable / timeout      -> deny (never run unapproved)
"""

import html
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rc_config as cfg  # noqa: E402

READ_ONLY_TOOLS = {
    "Read", "Grep", "Glob", "LS", "NotebookRead", "WebFetch", "WebSearch",
    "TodoWrite", "Task",  # Task sub-agents inherit this same hook
}
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _out(decision: str, reason: str):
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}))
    sys.exit(0)


def _summarize(tool: str, ti: dict):
    """Return (short_summary, detail, risk) for the Telegram card."""
    if tool == "Bash":
        cmd = ti.get("command", "")
        try:
            import ssh_exec
            risk = ssh_exec.classify(cmd)
        except Exception:  # noqa: BLE001
            risk = "write"
        return f"Bash: {cmd[:120]}", cmd, risk
    if tool in WRITE_TOOLS:
        path = ti.get("file_path") or ti.get("notebook_path") or "?"
        return f"{tool} → {path}", path, "write"
    if tool == "Task":
        desc = ti.get("description", "")
        return f"Spawn agent: {desc[:100]}", ti.get("prompt", "")[:500], "write"
    return f"{tool}", json.dumps(ti, ensure_ascii=False)[:500], "write"


def main():
    if cfg.get("CLAUDE_TG_BRIDGE") != "1" and os.environ.get("CLAUDE_TG_BRIDGE") != "1":
        sys.exit(0)  # not a bridge-spawned agent: stay out of the way

    try:
        payload = json.load(sys.stdin)
    except (ValueError, OSError):
        sys.exit(0)

    tool = payload.get("tool_name", "")
    ti = payload.get("tool_input", {}) or {}

    # Fast allow path for read-only work.
    if tool in READ_ONLY_TOOLS and tool != "Task":
        _out("allow", "read-only tool")
    if tool == "Bash":
        try:
            import ssh_exec
            if ssh_exec.classify(ti.get("command", "")) == "read":
                _out("allow", "read-only command")
        except Exception:  # noqa: BLE001
            pass

    summary, detail, risk = _summarize(tool, ti)
    chat = os.environ.get("CLAUDE_TG_CHAT_ID") or cfg.get_list("TELEGRAM_ALLOWED_CHATS")[:1]
    if isinstance(chat, list):
        chat = chat[0] if chat else ""
    if not chat:
        _out("deny", "không có CLAUDE_TG_CHAT_ID để hỏi duyệt")

    try:
        import approvals
        import tg_api
    except Exception as e:  # noqa: BLE001
        _out("deny", f"không nạp được module duyệt: {e}")

    req_id = approvals.create(tool, summary, detail, risk)
    flag = "⚠️ <b>NGUY HIỂM</b>\n" if risk == "danger" else ""
    text = (
        f"{flag}🤖 <b>Agent xin duyệt thao tác</b>\n"
        f"<b>Tool:</b> {html.escape(tool)}\n"
        f"<b>Việc:</b> <code>{html.escape(summary)}</code>\n"
        + (f"\n<pre>{html.escape(detail[:1200])}</pre>" if detail and detail != summary else "")
    )
    resp = tg_api.send_message(chat, text, reply_markup=tg_api.approve_keyboard(req_id))
    if not resp.get("ok"):
        _out("deny", f"không gửi được yêu cầu duyệt: {resp.get('description')}")

    timeout = int(cfg.get("TELEGRAM_APPROVAL_TIMEOUT", "300") or "300")
    verdict = approvals.wait(req_id, timeout=timeout)
    if verdict == "approved":
        _out("allow", "đã duyệt qua Telegram")
    elif verdict == "timeout":
        _out("deny", f"hết {timeout}s không có phản hồi → từ chối")
    else:
        _out("deny", "bị từ chối qua Telegram")


if __name__ == "__main__":
    main()
