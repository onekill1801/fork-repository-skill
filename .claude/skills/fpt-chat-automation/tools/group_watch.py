#!/usr/bin/env python3
"""Watch ONE FPT Chat group and drive code-review / build-to-dev from it.

Cầu nối FPT Chat -> Claude Code: lắng nghe đúng MỘT group qua socket realtime,
khi có tin nhắn trông như yêu cầu review MR hoặc build lên dev thì giao nguyên
văn cho một agent `claude -p` headless (full agent, mọi skill — dev-automation,
gitlab, jenkins) chạy ở gốc repo. Kết quả báo về CẢ Telegram LẪN chính group đó.

Khác với listen.py (lo các DM 1-1), tool này CHỈ theo dõi group mục tiêu và
KHÔNG auto-reply chuyện phiếm — nó chỉ kích hoạt khi tin khớp prefilter từ khoá.

Tận dụng hạ tầng có sẵn:
  - Vòng WS + helper đọc lịch sử của fpt-chat (`listen`, `client`, `send`).
  - Hook duyệt nút bấm của remote-control: agent chạy với CLAUDE_TG_BRIDGE=1 +
    `bridge_settings.json`, nên mọi thao tác [WRITE]/build hiện nút Duyệt/Từ chối
    trên Telegram. Bridge daemon (telegram_bridge.py) PHẢI đang chạy để giao nút
    bấm về — tool này không tự poll Telegram (tránh tranh getUpdates với bridge).

Cấu hình (.env ở gốc repo):
  FCHAT_WATCH_GROUP     id group cần theo dõi (bắt buộc; hoặc truyền --group)
  FCHAT_WATCH_KEYWORDS  prefilter, phân tách bằng dấu phẩy (mặc định bên dưới)
  FCHAT_WATCH_TIMEOUT   giây tối đa cho một lượt claude -p (mặc định 1800)
  (dùng lại) TELEGRAM_ALLOWED_CHATS, TELEGRAM_AUTO_APPROVE, CLAUDE_ACCOUNTS, ...

Chạy:
  python group_watch.py                       # group lấy từ FCHAT_WATCH_GROUP
  python group_watch.py --group <id>          # đè group
  python group_watch.py --once "<text>"       # test: xử lý 1 câu, không cần WS
"""

import argparse
import html
import json
import os
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import client          # noqa: E402  (fpt-chat)
import config          # noqa: E402  (fpt-chat)
import listen          # noqa: E402  (fpt-chat) — reuse _looks_encrypted/_fetch_history/WS bits
import send            # noqa: E402  (fpt-chat)
import tokens          # noqa: E402  (fpt-chat)
import ws_client       # noqa: E402  (fpt-chat)

# remote-control tools (Telegram + approval hook + account resolution)
_RC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                       "..", "..", "remote-control", "tools"))
sys.path.insert(0, _RC_DIR)
import rc_config as rccfg     # noqa: E402
import telegram_bridge as tb  # noqa: E402  (reuse settings/account/claude-bin helpers)
import tg_api                 # noqa: E402

# Prefilter rẻ: chỉ tin chứa MỘT trong các cụm này mới được đưa cho agent. Mục
# đích là lọc bớt chuyện phiếm; agent vẫn là người quyết cuối (trả 'SKIP' nếu
# tin không thực sự là yêu cầu). Đè bằng FCHAT_WATCH_KEYWORDS.
DEFAULT_KEYWORDS = [
    "mr", "merge request", "review", "build", "deploy", "lên dev", "len dev",
    "merge", "pipeline", "!review", "!build", "/review", "/build",
]

# Dạy agent bối cảnh + hành vi. Ghép thêm LONGTASK_SYS của bridge (bg_notify cho
# build chạy lâu) để khỏi lặp lại.
WATCH_SYS = (
    "BỐI CẢNH: bạn được kích hoạt bởi MỘT tin nhắn trong group FPT Chat phục vụ "
    "review code và build lên dev. Tin nhắn là yêu cầu của một thành viên nhóm. "
    "Phần 'LỊCH SỬ GẦN ĐÂY' (nếu có) chỉ để tra ngữ cảnh (vd link/số MR nhắc ở "
    "tin trước).\n"
    "QUY TẮC:\n"
    "1. Nếu tin KHÔNG thực sự là yêu cầu review MR hay build/deploy (chỉ là tán "
    "gẫu lỡ khớp từ khoá), hãy trả lời ĐÚNG MỘT TỪ: SKIP — và không làm gì khác.\n"
    "2. REVIEW: dùng skill dev-automation (gitlab_api.py) để đọc MR và ĐĂNG nhận "
    "xét trực tiếp lên MR bằng `gitlab_api.py mr-comment <iid> \"...\"`. Sau đó "
    "tóm tắt NGẮN GỌN kết quả (sẽ được đăng lại lên chat).\n"
    "3. BUILD/DEPLOY lên dev: đây là thao tác [WRITE] nhạy cảm — nó sẽ TỰ ĐỘNG bị "
    "chặn chờ DUYỆT bằng nút bấm Telegram (đừng tự hỏi lại bằng văn xuôi). Sau khi "
    "được duyệt, BẮT BUỘC bọc lệnh build bằng bg_notify.py (xem hướng dẫn dưới).\n"
    "4. Trả lời cuối cùng phải NGẮN GỌN, tiếng Việt, không markdown rườm rà — nó "
    "sẽ được đăng vào group chat và gửi Telegram.\n"
    "5. Để giao tiếp/giao việc hợp với người gửi, có thể tra kho đội nhóm: "
    "`python .claude/skills/team-registry/tools/team.py get <username>` lấy "
    "personality/interaction rồi điều chỉnh giọng cho phù hợp.\n\n"
    + tb.LONGTASK_SYS
)


def _now():
    return time.strftime("%H:%M:%S")


def _keywords():
    kws = rccfg.get_list("FCHAT_WATCH_KEYWORDS") if rccfg.get("FCHAT_WATCH_KEYWORDS") else None
    return [k.lower() for k in (kws or DEFAULT_KEYWORDS)]


def _looks_like_request(text: str, kws) -> bool:
    t = (text or "").lower()
    return any(k in t for k in kws)


def _guess_intent(text: str) -> str:
    """Đoán nhanh việc cần làm (chỉ để soạn tin báo nhận). review|build|xử lý."""
    t = (text or "").lower()
    if any(k in t for k in ("review", "mr", "merge", "pull request")):
        return "review"
    if any(k in t for k in ("build", "deploy", "lên dev", "len dev", "pipeline")):
        return "build"
    return "xử lý"


# Tin báo nhận gửi vào group NGAY khi bắt đầu, để mọi người biết bot đang làm gì.
_ACK = {
    "review": "🤖 Đã nhận yêu cầu — đang review, sẽ báo lại khi xong.",
    "build": "🤖 Đã nhận yêu cầu build lên dev — chờ duyệt rồi chạy, sẽ báo lại khi xong.",
    "xử lý": "🤖 Đã nhận yêu cầu — đang xử lý, sẽ báo lại khi xong.",
}


def _send_group(gid, group_type, text):
    """Gửi 1 tin vào group; trả messageIdInc (để có thể thu hồi) hoặc None."""
    try:
        res = send.send_text(gid, text, group_type)
        inc = ((res.get("echo") or {}).get("data") or {}).get("messageIdInc")
        return inc
    except Exception as e:  # noqa: BLE001
        print(f"[{_now()}] [WARN] gửi group lỗi: {e}", file=sys.stderr)
        return None


def _primary_account():
    """(label, home) tài khoản chính theo CLAUDE_ACCOUNTS (mặc định work)."""
    accts = tb._accounts()
    return accts[0] if accts else ("default", None)


def _invoke_agent(text, history_block):
    """Chạy một lượt `claude -p` cho yêu cầu này. Trả (result_text, is_error)."""
    settings_path = tb._write_bridge_settings()      # gắn hook duyệt Telegram
    label, home = _primary_account()
    chat = (tg_api.allowed_chats() or [""])[0]        # chat nhận yêu cầu duyệt

    prompt = text
    if history_block:
        prompt = f"{text}\n\n--- LỊCH SỬ GẦN ĐÂY (chỉ để tra ngữ cảnh) ---\n{history_block}"

    argv = [tb._claude_bin(), "-p", prompt,
            "--output-format", "json",
            "--append-system-prompt", WATCH_SYS,
            "--settings", settings_path]
    if rccfg.get("TELEGRAM_AGENT_MODEL"):
        argv += ["--model", rccfg.get("TELEGRAM_AGENT_MODEL")]

    env = dict(os.environ)
    env["CLAUDE_TG_BRIDGE"] = "1"
    env["CLAUDE_TG_CHAT_ID"] = str(chat)
    if home:
        env["HOME"] = home            # macOS / Linux
        env["USERPROFILE"] = home     # Windows

    timeout = int(rccfg.get("FCHAT_WATCH_TIMEOUT", "1800") or "1800")
    try:
        proc = subprocess.run(argv, cwd=rccfg.repo_root(), env=env,
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except FileNotFoundError:
        return "❌ Không tìm thấy 'claude' trên PATH (đặt CLAUDE_BIN trong .env).", True
    except subprocess.TimeoutExpired:
        return "⏱️ Agent chạy quá lâu, đã hủy.", True

    out = (proc.stdout or "").strip()
    try:
        obj = json.loads(out)
        result = obj.get("result") or obj.get("error") or out
        return result, bool(obj.get("is_error"))
    except ValueError:
        if proc.returncode != 0:
            return (out + "\n" + (proc.stderr or "")).strip() or "claude lỗi", True
        return out, False


def _post_results(gid, group_type, sender_name, original, result_text):
    """Đăng kết quả về CẢ Telegram lẫn group FPT Chat."""
    # Telegram
    tg = (f"💬 <b>New Group</b> — yêu cầu từ <b>{html.escape(sender_name)}</b>\n"
          f"<i>{html.escape(original[:200])}</i>\n\n{html.escape(result_text)}")
    for chat in tg_api.allowed_chats():
        try:
            tg_api.send_message(chat, tg)
        except Exception as e:  # noqa: BLE001
            print(f"[{_now()}] [WARN] gửi Telegram lỗi: {e}", file=sys.stderr)
    # FPT Chat group (plaintext — group này non-secure nên gửi được)
    inc = _send_group(gid, group_type, result_text)
    print(f"[{_now()}]        -> đăng group: {'ok inc=' + str(inc) if inc else 'gửi (chưa xác nhận)'}")


_busy = threading.Lock()


def _process(gid, group_type, sender_name, text, me):
    """Một lượt xử lý đầy đủ (chạy trong thread riêng)."""
    if not _busy.acquire(blocking=False):
        print(f"[{_now()}]        -> đang bận xử lý yêu cầu trước, bỏ qua tin này.")
        return
    try:
        print(f"[{_now()}] [TRIGGER] {sender_name}: {text!r} -> giao cho agent…")
        # Báo nhận ngay vào group để mọi người biết bot đang làm gì. Giữ lại inc
        # để thu hồi nếu agent quyết định đây không phải yêu cầu (SKIP).
        ack_inc = _send_group(gid, group_type, _ACK[_guess_intent(text)])
        hist = listen._fetch_history(gid, me, limit=15)
        history_block = "\n".join(hist[:-1]) if len(hist) > 1 else ""  # bỏ chính tin vừa tới
        result, is_error = _invoke_agent(text, history_block)
        result = (result or "").strip()
        if not is_error and result.upper().startswith("SKIP"):
            print(f"[{_now()}]        -> agent đánh giá KHÔNG phải yêu cầu (SKIP).")
            if ack_inc:  # thu hồi tin báo nhận để khỏi để lại rác trong group
                try:
                    send.recall_message(gid, ack_inc)
                    print(f"[{_now()}]        -> đã thu hồi tin báo nhận (inc={ack_inc}).")
                except Exception as e:  # noqa: BLE001
                    print(f"[{_now()}] [WARN] thu hồi báo nhận lỗi: {e}", file=sys.stderr)
            return
        if is_error:
            result = "⚠️ " + result
        _post_results(gid, group_type, sender_name, text, result or "(agent không trả về gì)")
    finally:
        _busy.release()


def _handle(obj, gid_target, me, kws, seen):
    data = obj.get("data") or {}
    if data.get("type") != "TEXT":
        return
    gid = data.get("groupId")
    if gid != gid_target:
        return
    sender = data.get("senderId")
    if not sender or sender == me:
        return  # bỏ tin của chính mình (chống lặp với tin kết quả bot đăng)
    inc = data.get("messageIdInc")
    key = (gid, inc)
    if key in seen:
        return
    seen.add(key)

    content = data.get("content")
    if listen._looks_encrypted(content):
        print(f"[{_now()}] [SKIP] tin mã hoá E2E — bỏ qua.")
        return
    group = data.get("group") or {}
    group_type = group.get("type") or group.get("groupType") or "SUPER_PRIVATE"
    sender_name = (data.get("user") or {}).get("displayName") or sender

    if not _looks_like_request(content, kws):
        print(f"[{_now()}] [skip] {sender_name}: {content!r}  (không khớp từ khoá)")
        return
    threading.Thread(target=_process,
                     args=(gid, group_type, sender_name, content, me),
                     daemon=True).start()


def run(gid_target, idle_ping=20):
    me = client.api_get("/user/me").get("id")
    if not me:
        print("[ERROR] không xác định được user hiện tại (token hỏng?)", file=sys.stderr)
        sys.exit(1)
    kws = _keywords()
    label, _ = _primary_account()
    print(f"[{_now()}] watching group {gid_target} as {me} | account={label} | "
          f"từ khoá={kws} | Ctrl+C để dừng")
    seen = set()
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
                    ws.send_text("ping")
                    last_ping = time.time()
                    continue
                if msg is None:
                    print(f"[{_now()}] socket đóng; kết nối lại…")
                    break
                if msg in ("pong", "ping"):
                    continue
                try:
                    o = json.loads(msg) if isinstance(msg, str) else None
                except Exception:
                    o = None
                if isinstance(o, dict) and o.get("type") == "message":
                    _handle(o, gid_target, me, kws, seen)
                if time.time() - last_ping > idle_ping:
                    ws.send_text("ping")
                    last_ping = time.time()
            ws.close()
        except KeyboardInterrupt:
            print(f"\n[{_now()}] stopped.")
            return
        except Exception as e:  # noqa: BLE001
            print(f"[{_now()}] lỗi kết nối: {e}; thử lại sau {backoff}s", file=sys.stderr)
            tokens.refresh(verbose=False)
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    missing = config.validate()
    if missing:
        print(f"[ERROR] thiếu config fpt-chat: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    p = argparse.ArgumentParser(prog="group_watch.py")
    p.add_argument("--group", dest="group", default=rccfg.get("FCHAT_WATCH_GROUP"),
                   help="id group cần theo dõi (mặc định FCHAT_WATCH_GROUP)")
    p.add_argument("--once", dest="once", default=None,
                   help="test: xử lý đúng một câu lệnh rồi thoát (không mở WS)")
    a = p.parse_args()
    if not a.group:
        print("[ERROR] chưa có group: đặt FCHAT_WATCH_GROUP trong .env hoặc truyền --group",
              file=sys.stderr)
        sys.exit(1)
    if a.once is not None:
        me = client.api_get("/user/me").get("id")
        _process(a.group, "SUPER_PRIVATE", "test", a.once, me)
        sys.exit(0)
    try:
        run(a.group)
    except KeyboardInterrupt:
        print("\nstopped.")
