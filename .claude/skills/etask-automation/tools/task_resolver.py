#!/usr/bin/env python3
"""Resolve MY eTask tasks: verify-against-code, then close / hand off (human-gated).

Khác với `etask_watch.py` (chỉ phân loại EXECUTE/ASSIGN), tool này chạy MỘT luồng
xử-lý-task đầy đủ và CHỈ lấy task ở 2 nhóm trạng thái `todo` (Chưa làm) + `processing`
(Đang làm) — bỏ qua mọi task đã duyệt/hoàn thành/đóng.

Vòng đời mỗi task MỚI / ĐỔI TRẠNG THÁI:
  1. Spawn `claude -p` (CHỈ ĐỌC) → đọc nội dung task, map task → project trong
     `work/projects.json`, rồi MỞ code trong `clone_dir` để xác minh task ĐÃ fix hay CHƯA.
     Trả verdict máy-đọc-được: fixed | not_fixed | unclear (+ project, estimate, người gợi ý).
  2. Quyết định (mọi thao tác GHI đều xin DUYỆT qua Telegram trước — nút bấm):
       • FIXED      → hỏi: ✅ Hoàn thành (complete_task) | 🕓 Chờ phê duyệt (update_task status).
                      Tín hiệu đã-validate của task: percent==1.0 hoặc statusType ∈ {approved, completed}.
       • NOT_FIXED  → hỏi: 👤 Tôi tự làm | ➡️ Giao người khác.
                        - Tôi tự làm  → bổ sung comment + chỉnh estimate, để assignee = tôi (theo dõi).
                        - Giao người  → hỏi tiếp: ✅ Giao <tên> | ❌ Bỏ qua → assign_task_users +
                          comment thông tin bổ sung + chỉnh estimate hợp lý.
       • UNCLEAR    → báo Telegram, KHÔNG ghi.

Chống lấy trùng / gán nhiều người 1 task:
  • STATE (`temp/etask_resolved.json`) khoá theo (task_id → statusType): đã xử lý ở đúng
    trạng thái đó thì KHÔNG đụng lại; lưu luôn `assigned_to` để vòng sau không gán lại người khác.
  • `_inflight`: task đang chờ duyệt sẽ KHÔNG bị vòng poll kế tiếp bốc lại.
  • Trước khi gán: get_task kiểm tra đã có assignee (khác tôi) chưa → có thì báo & bỏ qua gán.

Lần chạy đầu lập BASELINE (đánh dấu task hiện có, không xử lý cả backlog) — về sau chỉ xử lý
task mới/đổi trạng thái. Dùng --resolve-existing để làm cả backlog (giới hạn --max-per-cycle).

Cấu hình (.env):
  ETASK_RESOLVE_INTERVAL (mặc định 600s) · ETASK_RESOLVE_TIMEOUT (phân tích, mặc định 900s)
  ETASK_RESOLVE_STATUS_TYPES (mặc định "todo,processing")
  ETASK_RESOLVE_STATUS_REVIEW (status code "chờ phê duyệt" — [Unverified], confirm theo workflow của bạn)
  ETASK_RESOLVE_STATUS_INPROGRESS (mặc định IN_PROGRESS)
  ETASK_MY_LOGIN (login của tôi để loại khỏi gợi ý assignee; mặc định chungtv8)
  (dùng lại) TELEGRAM_ALLOWED_CHATS, TELEGRAM_APPROVAL_TIMEOUT, CLAUDE_ACCOUNTS, TELEGRAM_AGENT_MODEL

Chạy:
  python task_resolver.py                  # poll 10', baseline trước
  python task_resolver.py --interval 300   # poll 5'
  python task_resolver.py --once           # một vòng rồi thoát
  python task_resolver.py --no-act         # chỉ in task lấy được, không phân tích/ghi
  python task_resolver.py --task <id>      # xử lý ĐÚNG 1 task id rồi thoát
"""

import argparse
import datetime
import html
import json
import os
import re
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import client          # noqa: E402  (etask)
import config          # noqa: E402  (etask)
import search          # noqa: E402  (etask)
import tasks as task_api  # noqa: E402  (etask)

# remote-control tools: Telegram + approval IPC + account/settings helpers
_RC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                       "..", "..", "remote-control", "tools"))
sys.path.insert(0, _RC_DIR)
import approvals             # noqa: E402
import rc_config as rccfg    # noqa: E402
import telegram_bridge as tb  # noqa: E402
import tg_api                # noqa: E402

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = rccfg.repo_root()
STATE = os.path.join(REPO_ROOT, "temp", "etask_resolved.json")

# statusType coi là ĐÃ hoàn tất → KHÔNG xử lý. Lưu ý: 'approved' (Đã duyệt) KHÔNG nằm đây —
# theo yêu cầu, task chưa 'completed' (gồm todo/processing/approved) đều phải xử lý.
TERMINAL_TYPES = {"completed", "closed", "cancelled", "rejected", "done"}
# Mặc định: lấy MỌI task chưa hoàn tất (None → không lọc server-side, loại TERMINAL ở client).
# Đè bằng ETASK_RESOLVE_STATUS_TYPES (vd "todo,processing" nếu muốn hẹp lại).
DEFAULT_STATUS_TYPES = None

_busy = threading.Semaphore(2)     # tối đa 2 task đang phân tích/chờ duyệt cùng lúc
_inflight = set()                  # task đang xử lý (chống vòng poll bốc lại)
_inflight_lock = threading.Lock()


def _now():
    return time.strftime("%H:%M:%S")


def _log(msg):
    """Ghi ra stdout kèm giờ + flush ngay (để log nền/pipe thấy real-time)."""
    print(f"[{_now()}] {msg}", flush=True)


def _iso(d: datetime.date) -> str:
    # eTask server dùng Instant.parse → BẮT BUỘC có 'Z' (UTC), thiếu sẽ lỗi parse.
    return d.strftime("%Y-%m-%dT00:00:00Z")


def _load_state() -> dict:
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(d: dict):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2, ensure_ascii=False)


def _status_types():
    """Nhóm statusType lọc server-side, hoặc None = lấy tất cả (loại TERMINAL ở client)."""
    raw = rccfg.get_list("ETASK_RESOLVE_STATUS_TYPES")
    return raw or DEFAULT_STATUS_TYPES


def _my_login() -> str:
    return rccfg.get("ETASK_MY_LOGIN") or "chungtv8"


def _my_tasks() -> list:
    """Task đang giao cho tôi, MỌI trạng thái CHƯA hoàn tất (loại TERMINAL_TYPES).
    Mặc định lấy tất cả rồi lọc client-side; đè phạm vi bằng ETASK_RESOLVE_STATUS_TYPES."""
    st = _status_types()
    kwargs = {"size": 100}
    if st:
        kwargs["status_type"] = st
    r = search.search_my_assigned_tasks(**kwargs)
    if not isinstance(r, dict) or r.get("error"):
        return [{"error": True, "message": (r or {}).get("message", "lỗi my-tasks")}]
    data = ((r.get("content") or {}).get("data")) or []
    out = []
    for t in data:
        if (t.get("statusType") or "").lower() in TERMINAL_TYPES:
            continue
        out.append({
            "id": t.get("id"),
            "name": t.get("name") or "(không tên)",
            "statusType": (t.get("statusType") or "").lower(),
            "status": t.get("status"),
            "listTaskId": t.get("listTaskId"),
            "project": t.get("projectName") or "",
            "due": t.get("dueDate") or "",
            "percent": t.get("percent"),
            "priority": t.get("priority"),
            "description": t.get("description") or "",   # từ ES record → để NỐI note, không ghi đè
            # assignee/reviewer chỉ có trong record SEARCH (get_task KHÔNG trả) → mang theo để dedup
            "assignTaskList": t.get("assignTaskList") or [],
            "assignReviewList": t.get("assignReviewList") or [],
        })
    return out


def _user_ids(assign_list) -> list:
    """Trích userId (int) từ một assignTaskList/assignReviewList (list dict {userId,...})."""
    ids = []
    for it in assign_list or []:
        if isinstance(it, dict) and it.get("userId") is not None:
            try:
                ids.append(int(it["userId"]))
            except (TypeError, ValueError):
                pass
    return sorted(set(ids))


def _status_id_for(list_task_id, status_type):
    """Tra status-ID (mờ, theo từng list) cho một nhóm statusType. None nếu list KHÔNG có
    cột thuộc nhóm đó. Cần vì update_task(status=...) nhận status-ID theo-list, không nhận keyword.

    ⚠️ KHÔNG lọc bằng status_type ở server (ES trả TASK CHÉO LIST → mượn nhầm status-ID của list
    khác, set vào task sẽ KHÔNG đổi được). Thay vào đó: lấy task TRONG list rồi chỉ nhận status-ID
    của row THỰC SỰ thuộc list này + đúng statusType."""
    if not list_task_id:
        return None
    try:
        r = search.search_tasks(list_task_id=list_task_id, size=100)
    except SystemExit:
        return None
    if not isinstance(r, dict) or r.get("error"):
        return None
    rows = ((r.get("content") or {}).get("data")) or []
    st = status_type.lower()
    for x in rows:
        if (x.get("listTaskId") == list_task_id
                and (x.get("statusType") or "").lower() == st
                and x.get("status")):
            return x.get("status")
    return None


def _resolve_status(task, status_type, env_key):
    """status-ID cho nhóm: ưu tiên tra động trong list; fallback .env (env_key = status-ID)."""
    return _status_id_for(task.get("listTaskId"), status_type) or (rccfg.get(env_key) or None)


# ── account / agent ────────────────────────────────────────────────
def _primary_account():
    accts = tb._accounts()
    return accts[0] if accts else ("default", None)


def _claude_env(chat):
    label, home = _primary_account()
    env = dict(os.environ)
    env["CLAUDE_TG_BRIDGE"] = "1"
    env["CLAUDE_TG_CHAT_ID"] = str(chat)
    if home:
        env["HOME"] = home
        env["USERPROFILE"] = home
    return env, label, home


_ANALYSIS_SYS = (
    "Bạn là kỹ sư phân tích MỘT task eTask để quyết định nó ĐÃ được fix trong code hay CHƯA. "
    "TUYỆT ĐỐI CHỈ ĐỌC — không ghi/đổi gì (không tạo branch/MR/comment, không đổi task). "
    "Quy trình bắt buộc:\n"
    "1) Đọc chi tiết task: `python .claude/skills/etask-automation/tools/tasks.py get <id>` "
    "(xem mô tả, checklist, comment để hiểu YÊU CẦU/bug cần fix).\n"
    "2) Map task → project: đọc `work/projects.json`, đối chiếu projectName của task với "
    "key/gitlab_path → lấy `clone_dir`. KHÔNG map được rõ ràng → status=unclear.\n"
    "3) MỞ code trong clone_dir đó (grep/đọc file) để kết luận task đã fix CHƯA: tìm hàm/đoạn "
    "code liên quan, kiểm tra logic mô tả trong task đã hiện diện đúng chưa. Nêu BẰNG CHỨNG "
    "(file:dòng) ngắn gọn.\n"
    "4) Nếu CHƯA fix và nên giao người khác: chạy "
    "`python .claude/skills/team-registry/tools/team.py match --task \"<tên task>\" --exclude <my_login>` "
    "rồi `team.py get <key>` đọc hồ sơ ứng viên đầu (skill khớp + tải) và LẤY userId eTask của họ "
    "ở `handles.etask_user_id` (đặt vào assignee_etask_id; thiếu thì để '-').\n"
    "Kết thúc câu trả lời bằng ĐÚNG một khối máy-đọc-được:\n"
    "[[VERDICT]]\n"
    "status: fixed | not_fixed | unclear\n"
    "project: <key trong projects.json hoặc '-'>\n"
    "needs_approval: yes | no   # task có cần bước review/duyệt trước khi đóng không\n"
    "estimate_days: <số ngày hợp lý để hoàn tất nếu CHƯA fix, hoặc '-'>\n"
    "assignee_name: <tên người gợi ý nếu nên giao, '-' nếu không>\n"
    "assignee_etask_id: <userId eTask của người đó, '-' nếu không có>\n"
    "reason: <một dòng vì sao>\n"
    "[[/VERDICT]]\n"
)

_VERDICT_RE = re.compile(r"\[\[VERDICT\]\](.*?)\[\[/VERDICT\]\]", re.S | re.I)


def _parse_verdict(text: str) -> dict:
    m = _VERDICT_RE.search(text or "")
    body = m.group(1) if m else ""
    out = {"status": "unclear", "project": "-", "needs_approval": "no",
           "estimate_days": "-", "assignee_name": "-", "assignee_etask_id": "-", "reason": ""}
    for line in body.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip().lower()
            if k in out:
                out[k] = v.strip().split("#")[0].strip()
    s = out["status"].lower()
    out["status"] = "fixed" if s.startswith("fix") else ("not_fixed" if "not" in s or "chưa" in s else "unclear")
    return out


def _analyze(task, chat) -> tuple:
    """Spawn claude -p (read-only) → (full_text, verdict_dict|None).

    Agent phân tích chạy TỰ TRỊ (`--dangerously-skip-permissions`): đọc code/chạy tool KHÔNG
    hỏi người dùng. An toàn vì system prompt ép CHỈ ĐỌC + mọi WRITE nghiệp vụ do Python làm SAU
    khi bạn duyệt qua thẻ Telegram. Chỉ 'chốt nghiệp vụ' (Hoàn thành/Chờ duyệt · Tự làm/Giao người)
    mới hỏi bạn."""
    env, _, _ = _claude_env(chat)
    sys_prompt = _ANALYSIS_SYS.replace("<my_login>", _my_login())
    prompt = (
        f"Phân tích task eTask sau và xác minh trong CODE đã fix chưa.\n"
        f"- ID: {task['id']}\n- Tên: {task['name']}\n"
        f"- Project: {task['project']} · trạng thái: {task['statusType']} · "
        f"percent: {task.get('percent')} · due: {task['due']}\n\n"
        f"Theo đúng quy trình 4 bước rồi in tóm tắt NGẮN (yêu cầu task, đã fix chưa, bằng chứng "
        f"file:dòng, rủi ro) và kết thúc bằng khối [[VERDICT]] theo quy định."
    )
    argv = [tb._claude_bin(), "-p", prompt, "--output-format", "json",
            "--append-system-prompt", sys_prompt, "--dangerously-skip-permissions"]
    if rccfg.get("TELEGRAM_AGENT_MODEL"):
        argv += ["--model", rccfg.get("TELEGRAM_AGENT_MODEL")]
    timeout = int(rccfg.get("ETASK_RESOLVE_TIMEOUT", "900") or "900")
    try:
        proc = subprocess.run(argv, cwd=REPO_ROOT, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError:
        return "❌ Không tìm thấy 'claude' trên PATH.", None
    except subprocess.TimeoutExpired:
        return "⏱️ Phân tích quá lâu, đã hủy.", None
    out = (proc.stdout or "").strip()
    try:
        result = json.loads(out).get("result") or out
    except ValueError:
        result = out
    return result, _parse_verdict(result)


# ── Telegram helpers ────────────────────────────────────────────────
def _binary_kb(req_id: str, yes_label: str, no_label: str) -> dict:
    """Inline keyboard nhị phân, callback theo chuẩn `appr:` mà bridge đã route ở
    CẢ hai mode (full + approvals-only). Nhãn nút tuỳ biến, ngữ nghĩa yes/no do caller định."""
    return {"inline_keyboard": [[
        {"text": yes_label, "callback_data": f"appr:{req_id}:yes"},
        {"text": no_label, "callback_data": f"appr:{req_id}:no"},
    ]]}


def _ask(chat, body: str, yes_label: str, no_label: str, task) -> str:
    """Gửi 1 thẻ nhị phân, chờ duyệt. Trả 'yes' | 'no' | 'timeout'."""
    req_id = approvals.create("etask-resolve", f"{task['name'][:60]}", task["id"], "write")
    resp = tg_api.send_message(chat, body, reply_markup=_binary_kb(req_id, yes_label, no_label),
                               bot=tg_api.approval_bot())
    if not resp.get("ok"):
        print(f"[{_now()}] [WARN] không gửi được thẻ duyệt: {resp.get('description')}", file=sys.stderr)
        return "timeout"
    timeout = int(rccfg.get("TELEGRAM_APPROVAL_TIMEOUT", "300") or "300")
    verdict = approvals.wait(req_id, timeout=max(timeout, 600))
    return "yes" if verdict == "approved" else ("no" if verdict == "denied" else "timeout")


def _notify(chat, text):
    try:
        tg_api.send_message(chat, text, bot=tg_api.approval_bot())
    except Exception as e:  # noqa: BLE001
        print(f"[{_now()}] [WARN] gửi Telegram lỗi: {e}", file=sys.stderr)


def _add_note(task: dict, note: str) -> bool:
    """Bổ sung thông tin cho task. Kênh theo ETASK_RESOLVE_NOTE_CHANNEL:
      - 'description' (mặc định): NỐI vào description hiện có (thuần skill, UI hiện ngay,
        không ghi đè mô tả cũ — lấy desc từ ES record trong `task`).
      - 'comment': tạo comment (chỉ HIỆN trên UI nếu backend đã vá create_comment bỏ commentIn
        + deploy; nếu chưa deploy comment sẽ bị ẩn)."""
    channel = (rccfg.get("ETASK_RESOLVE_NOTE_CHANNEL") or "description").strip().lower()
    if channel == "comment":
        cl = os.path.join(TOOLS_DIR, "checklists.py")
        try:
            proc = subprocess.run([sys.executable, cl, "add-comment", task["id"], note],
                                  cwd=TOOLS_DIR, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=60)
            return proc.returncode == 0
        except Exception as e:  # noqa: BLE001
            print(f"[{_now()}] [WARN] add-comment lỗi: {e}", file=sys.stderr)
            return False
    # description (mặc định)
    cur = (task.get("description") or "").rstrip()
    stamp = time.strftime("%Y-%m-%d %H:%M")
    new = (f"{cur}\n\n" if cur else "") + f"[auto-resolver {stamp}] {note}"
    try:
        task_api.update_task(task["id"], description=new)
        return True
    except SystemExit:
        print(f"[{_now()}] [WARN] add-note (description) lỗi cho {task['id']}", file=sys.stderr)
        return False


def _set_estimate(task_id: str, days):
    """Chỉnh estimate hợp lý: start = hôm nay, due = hôm nay + days."""
    try:
        n = int(float(days))
    except (TypeError, ValueError):
        return
    if n <= 0:
        return
    today = datetime.date.today()
    try:
        task_api.update_task(task_id, start_date=_iso(today),
                             due_date=_iso(today + datetime.timedelta(days=n)))
    except SystemExit:
        pass


def _db_status(task_id):
    """Đọc status THẬT từ DB (get_task, không qua ES) để verify sau khi ghi."""
    try:
        r = task_api.get_task(task_id)
    except SystemExit:
        return None
    d = r.get("content") if isinstance(r, dict) and isinstance(r.get("content"), dict) else r
    return d.get("status") if isinstance(d, dict) else None


# ── per-task resolution ─────────────────────────────────────────────
def _handle_fixed(task, chat, v):
    summary = (f"🟢 <b>Task có vẻ ĐÃ FIX trong code</b>\n<b>{html.escape(task['name'])}</b>\n"
               f"<i>{html.escape(task['project'])} · {task['statusType']}</i>\n"
               f"Lý do: {html.escape(v['reason'][:300])}\n\nChốt trạng thái nào?")
    pick = _ask(chat, summary, "✅ Hoàn thành", "🕓 Chờ phê duyệt", task)
    _log(f"[FIXED] {task['id']} → duyệt: {'Hoàn thành' if pick=='yes' else ('Chờ phê duyệt' if pick=='no' else 'timeout')}")
    if pick == "timeout":
        _notify(chat, f"⏭️ Hết hạn duyệt — chưa đổi trạng thái task <b>{html.escape(task['name'][:60])}</b>.")
        return
    before = _db_status(task["id"])
    if pick == "yes":
        try:
            task_api.complete_task(task["id"])
        except SystemExit:
            _notify(chat, "⚠️ complete_task lỗi — xem log máy chủ."); return
        after = _db_status(task["id"])
        ok = after and after != before
        _log(f"[ACTION] {task['id']} complete_task → status={after} ({'OK' if ok else 'CHƯA đổi'})")
        _notify(chat, (f"✅ Đã đánh dấu HOÀN THÀNH: <b>{html.escape(task['name'][:60])}</b> "
                       f"(status=<code>{html.escape(str(after))}</code>)." if ok else
                       f"⚠️ Gọi hoàn thành nhưng DB chưa đổi (status vẫn <code>{html.escape(str(after))}</code>). "
                       f"Kiểm tra trong UI."))
    else:
        review = _resolve_status(task, "approved", "ETASK_RESOLVE_STATUS_REVIEW")
        if not review:
            _notify(chat, "⚠️ List này KHÔNG có cột 'chờ phê duyệt' (approved). Không đổi được — "
                          "chọn Hoàn thành, hoặc set <code>ETASK_RESOLVE_STATUS_REVIEW=&lt;statusId&gt;</code> nếu có.")
            return
        try:
            task_api.update_task(task["id"], status=review)
        except SystemExit:
            _notify(chat, f"⚠️ update_task(status={html.escape(str(review))}) lỗi — xem log máy chủ."); return
        after = _db_status(task["id"])
        ok = after == review
        _log(f"[ACTION] {task['id']} set approved={review} → DB status={after} ({'OK' if ok else 'CHƯA đổi'})")
        _notify(chat, (f"🕓 Đã chuyển <b>{html.escape(task['name'][:60])}</b> sang chờ phê duyệt "
                       f"(status=<code>{html.escape(str(review))}</code>)." if ok else
                       f"⚠️ Gọi chuyển 'chờ phê duyệt' nhưng DB chưa nhận (status vẫn "
                       f"<code>{html.escape(str(after))}</code>). Có thể do workflow eTask — đổi trong UI."))


def _handle_not_fixed(task, chat, v):
    head = (f"🟠 <b>Task CHƯA fix</b>\n<b>{html.escape(task['name'])}</b>\n"
            f"<i>{html.escape(task['project'])} · {task['statusType']}</i>\n"
            f"Lý do: {html.escape(v['reason'][:300])}\n\nBạn xử lý thế nào?")
    pick = _ask(chat, head, "👤 Tôi tự làm", "➡️ Giao người khác", task)
    _log(f"[NOT_FIXED] {task['id']} → duyệt: {'Tôi tự làm' if pick=='yes' else ('Giao người khác' if pick=='no' else 'timeout')}")
    if pick == "timeout":
        _notify(chat, f"⏭️ Hết hạn duyệt — chưa xử lý task <b>{html.escape(task['name'][:60])}</b>.")
        return "skip"

    if pick == "yes":   # tôi tự làm / theo dõi
        if task["statusType"] == "todo":
            inprog = _resolve_status(task, "processing", "ETASK_RESOLVE_STATUS_INPROGRESS")
            if inprog:
                try:
                    task_api.update_task(task["id"], status=inprog)
                except SystemExit:
                    pass
        _set_estimate(task["id"], v.get("estimate_days"))
        _add_note(task, f"Tôi (chungtv8) nhận xử lý task này. Phân tích: {v['reason']}")
        _notify(chat, f"👤 OK — bạn giữ <b>{html.escape(task['name'][:60])}</b> để tự làm/theo dõi. "
                      f"Đã chỉnh estimate + ghi chú.")
        return "self"

    # giao người khác
    name = v.get("assignee_name", "-")
    uid = v.get("assignee_etask_id", "-")
    if not uid or uid == "-" or not str(uid).isdigit():
        _notify(chat, f"➡️ Cần giao người nhưng THIẾU eTask userId của <b>{html.escape(name)}</b> "
                      f"(bổ sung qua <code>team.py set {html.escape(name)} --etask-id &lt;id&gt;</code>). "
                      f"Tạm để bạn gán tay trong UI.")
        return "skip"
    existing = _user_ids(task.get("assignTaskList"))
    others = [i for i in existing if str(i) != str(uid)]
    confirm = _ask(chat,
                   f"➡️ Giao task <b>{html.escape(task['name'][:60])}</b> cho <b>{html.escape(name)}</b> "
                   f"(userId <code>{html.escape(str(uid))}</code>)?"
                   + (f"\n⚠️ Task hiện đã có assignee khác (userIds {others})." if others else ""),
                   "✅ Giao", "❌ Bỏ qua", task)
    if confirm != "yes":
        _notify(chat, f"⏭️ Bỏ qua giao việc cho task <b>{html.escape(task['name'][:60])}</b>.")
        return "skip"
    try:
        task_api.assign_task_users(task["id"], [int(uid)], mode="add")
        _log(f"[ACTION] {task['id']} assign_task_users {name}({uid}) OK")
    except SystemExit:
        _notify(chat, "⚠️ assign_task_users lỗi — xem log máy chủ.")
        return "skip"
    _set_estimate(task["id"], v.get("estimate_days"))
    _add_note(task, f"Giao cho {name}. Thông tin bổ sung từ phân tích: {v['reason']}")
    _notify(chat, f"✅ Đã giao <b>{html.escape(task['name'][:60])}</b> cho <b>{html.escape(name)}</b> "
                  f"(thêm comment + chỉnh estimate).")
    return name


def _handle_task(task, chat, state):
    with _busy:
        try:
            _log(f"[RESOLVE] {task['id']} {task['name']!r} → xác minh code…")
            full, v = _analyze(task, chat)
            if v is None:
                _log(f"[VERDICT] {task['id']} → LỖI phân tích: {full[:200]}")
                _notify(chat, f"⚠️ Phân tích task <code>{html.escape(task['id'])}</code> lỗi:\n"
                              f"{html.escape(full[:500])}")
                return
            _log(f"[VERDICT] {task['id']} → {v['status']} | project={v.get('project')} "
                 f"| assignee={v.get('assignee_name')}({v.get('assignee_etask_id')}) "
                 f"| est={v.get('estimate_days')} | {v.get('reason','')[:120]}")
            summary = full.split("[[VERDICT]]")[0].strip()
            _notify(chat, f"🗂️ <b>{html.escape(task['name'])}</b>\n<pre>{html.escape(summary[:1200])}</pre>")
            assigned = task["statusType"]
            if v["status"] == "fixed":
                _handle_fixed(task, chat, v)
            elif v["status"] == "not_fixed":
                res = _handle_not_fixed(task, chat, v)
                if isinstance(res, str) and res not in ("skip", "self"):
                    state[task["id"]] = {"statusType": task["statusType"], "assigned_to": res}
                    _save_state(state)
                    return
            else:
                _notify(chat, f"❓ Chưa rõ task <b>{html.escape(task['name'][:60])}</b> đã fix chưa "
                              f"(map project/đánh giá không chắc). Lý do: {html.escape(v['reason'][:200])}")
        finally:
            # đánh dấu đã xử lý ở đúng trạng thái này (tránh lặp), trừ khi đã set ở nhánh giao việc
            if not isinstance(state.get(task["id"]), dict):
                state[task["id"]] = {"statusType": task["statusType"], "assigned_to": _my_login()}
                _save_state(state)
            with _inflight_lock:
                _inflight.discard(task["id"])


def _seen_at(state, task) -> bool:
    rec = state.get(task["id"])
    if isinstance(rec, dict):
        return rec.get("statusType") == task["statusType"]
    return rec == task["statusType"]   # tương thích state cũ (string)


def poll_once(chat, state, act, max_per_cycle, baseline=False):
    tasks = _my_tasks()
    if tasks and tasks[0].get("error"):
        print(f"[{_now()}] [ERROR] eTask: {tasks[0].get('message')}", file=sys.stderr)
        return
    launched = 0
    for t in tasks:
        if not t.get("id"):
            continue
        if t["statusType"] in TERMINAL_TYPES:
            continue
        if _seen_at(state, t):
            continue
        with _inflight_lock:
            if t["id"] in _inflight:
                continue
        if baseline:
            state[t["id"]] = {"statusType": t["statusType"], "assigned_to": _my_login()}
            continue
        tag = "đổi trạng thái" if t["id"] in state else "mới"
        print(f"[{_now()}] [task {tag}] {t['id']} {t['name']!r} ({t['statusType']})")
        if not act:
            continue
        if launched >= max_per_cycle:
            print(f"[{_now()}]        (đạt {max_per_cycle} task/vòng — phần còn lại để vòng sau)")
            break
        with _inflight_lock:
            _inflight.add(t["id"])
        threading.Thread(target=_handle_task, args=(t, chat, state), daemon=True).start()
        launched += 1
    if baseline:
        _save_state(state)
        print(f"[{_now()}] baseline: đánh dấu {len(state)} task hiện có (không xử lý backlog). "
              f"Từ giờ chỉ xử lý task mới/đổi trạng thái. (--resolve-existing để làm cả backlog)")


def run(interval, act, max_per_cycle, resolve_existing):
    chat = (tg_api.allowed_chats(tg_api.approval_bot()) or [""])[0]
    if not chat:
        print("[ERROR] thiếu TELEGRAM_ALLOWED_CHATS (cần để gửi thẻ duyệt + nhận nút bấm).", file=sys.stderr)
        sys.exit(1)
    label, _ = _primary_account()
    print(f"[{_now()}] resolving MY eTask tasks (todo/processing) | account={label} | "
          f"mỗi {interval}s | tối đa {max_per_cycle} task/vòng | Ctrl+C để dừng")
    state = _load_state()
    if not state and not resolve_existing:
        poll_once(chat, state, act, max_per_cycle, baseline=True)
    while True:
        try:
            poll_once(chat, state, act, max_per_cycle)
        except KeyboardInterrupt:
            print(f"\n[{_now()}] stopped."); return
        except Exception as e:  # noqa: BLE001
            print(f"[{_now()}] poll error: {e}", file=sys.stderr)
        time.sleep(interval)


if __name__ == "__main__":
    missing = config.validate() if hasattr(config, "validate") else []
    if missing:
        print(f"[ERROR] thiếu config eTask: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    p = argparse.ArgumentParser(prog="task_resolver.py")
    p.add_argument("--interval", type=int,
                   default=int(rccfg.get("ETASK_RESOLVE_INTERVAL", "600") or "600"),
                   help="giây giữa các vòng poll (mặc định 600)")
    p.add_argument("--once", action="store_true", help="một vòng rồi thoát")
    p.add_argument("--no-act", dest="act", action="store_false",
                   help="chỉ in task lấy được, không phân tích/ghi")
    p.add_argument("--max-per-cycle", type=int, default=2, help="số task xử lý mỗi vòng (mặc định 2)")
    p.add_argument("--resolve-existing", action="store_true", help="xử lý cả backlog (bỏ baseline)")
    p.add_argument("--task", dest="task_id", default=None,
                   help="xử lý ĐÚNG một task id rồi thoát")
    a = p.parse_args()

    if a.task_id:
        chat = (tg_api.allowed_chats(tg_api.approval_bot()) or [""])[0]
        if not chat:
            print("[ERROR] thiếu TELEGRAM_ALLOWED_CHATS.", file=sys.stderr); sys.exit(1)
        match = [t for t in _my_tasks() if t.get("id") == a.task_id]
        task = match[0] if match else {"id": a.task_id, "name": a.task_id,
                                       "statusType": "?", "status": None, "project": "",
                                       "due": "", "percent": None, "priority": None}
        _handle_task(task, chat, _load_state())
        sys.exit(0)
    if a.once:
        chat = (tg_api.allowed_chats(tg_api.approval_bot()) or [""])[0]
        poll_once(chat, _load_state(), a.act, a.max_per_cycle)
        for th in [t for t in threading.enumerate()
                   if t is not threading.current_thread() and t.daemon]:
            th.join(timeout=int(rccfg.get("TELEGRAM_APPROVAL_TIMEOUT", "300") or "300") + 120)
        sys.exit(0)
    try:
        run(a.interval, a.act, a.max_per_cycle, a.resolve_existing)
    except KeyboardInterrupt:
        print("\nstopped.")
