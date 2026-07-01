# Cookbook — Task Resolver (verify-in-code → close / hand off)

Chi tiết Workflow 8. Tool: `tools/task_resolver.py`. Đây là luồng **poller xử-lý-task**
cho task giao cho tôi, lấy **mọi trạng thái CHƯA `completed`** (todo/processing/approved —
loại completed/closed/cancelled/rejected), xác minh trong code rồi đóng task hoặc giao người —
**mọi thao tác ghi đều duyệt qua Telegram bằng nút bấm**. Hẹp phạm vi bằng `ETASK_RESOLVE_STATUS_TYPES`
(vd `todo,processing`).

## 1. Mô hình tổng thể

```
poll my-tasks (mọi trạng thái chưa completed: todo/processing/approved)
   └─ task mới / đổi trạng thái  (bỏ qua nếu đã xử lý ở đúng statusType đó)
        └─ claude -p (CHỈ ĐỌC): đọc task → map project (work/projects.json) → mở code (clone_dir)
              └─ [[VERDICT]] status: fixed | not_fixed | unclear
                    ├─ fixed     → thẻ DUYỆT: ✅ Hoàn thành | 🕓 Chờ phê duyệt
                    ├─ not_fixed → thẻ DUYỆT: 👤 Tôi tự làm | ➡️ Giao người khác
                    │                                └─ thẻ tiếp: ✅ Giao <tên> | ❌ Bỏ qua
                    └─ unclear   → chỉ báo Telegram, KHÔNG ghi
```

Phần phân tích là **read-only** (spawn `claude -p`); mọi **WRITE** do `task_resolver.py`
thực hiện bằng Python (`tasks.py`) **chỉ sau khi bạn bấm Duyệt** — tách bạch giống `etask_watch.py`.

## 2. Điều kiện cần (tiền đề)

| Thứ | Vì sao |
|---|---|
| `.env` eTask (`ETASK_BASE_URL`, `ETASK_PAT_TOKEN`...) | đọc/ghi task |
| `.env` Telegram (`TELEGRAM_ALLOWED_CHATS`, bot token) | gửi thẻ + nhận nút bấm |
| **Bridge Telegram đang chạy** | nút `appr:` được `telegram_bridge.py` route ở cả `full` lẫn `approvals-only` |
| `work/projects.json` map đúng `projectName → clone_dir` | để xác minh trong CODE |
| `work/<project>` đã clone | grep/đọc code |
| `work/team.json` có `etask_id` cho người sẽ nhận giao | `assign_task_users` cần **userId số** |

> Không có bridge chạy → thẻ vẫn gửi nhưng không ai bấm được → mọi thao tác sẽ **timeout**
> (an toàn: không ghi gì).

## 3. Chạy

```
cd .claude/skills/etask-automation/tools     # macOS/Linux: python3
python task_resolver.py                       # baseline lần đầu, rồi poll mỗi 10'
python task_resolver.py --interval 300        # poll 5'
python task_resolver.py --once                # một vòng rồi thoát
python task_resolver.py --no-act              # chỉ in task lấy được, không phân tích/ghi
python task_resolver.py --task 123456         # xử lý ĐÚNG 1 task rồi thoát
python task_resolver.py --resolve-existing    # xử lý cả backlog (bỏ baseline) — cẩn thận
```

**Baseline:** lần chạy đầu (state rỗng) chỉ đánh dấu task hiện có là "đã thấy", KHÔNG xử lý
cả backlog (tránh bắn hàng loạt). Từ đó chỉ xử lý task **mới** hoặc **đổi trạng thái**.

## 4. Quyết định chi tiết

### 4a. FIXED — task đã được fix trong code
Thẻ Telegram 2 nút: **`✅ Hoàn thành`** → `complete_task` · **`🕓 Chờ phê duyệt`** →
`update_task(status=ETASK_RESOLVE_STATUS_REVIEW)`.

- Tín hiệu coi là **đã validate/hoàn tất** (đọc từ record task): `percent == 1.0`
  **hoặc** `statusType ∈ {approved, completed}`. Nếu task đã ở các trạng thái này thì filter
  `todo,processing` đã loại từ đầu — nên nhánh này chủ yếu để **đóng** task còn đang mở mà code đã xong.
- ⚠️ **`update_task(status=...)` nhận status-ID (mờ, theo từng list)**, KHÔNG nhận keyword
  `OPEN/IN_PROGRESS/DONE` (đã verify: `status` của task là ID kiểu `00002qOI...`). Tool **tự tra**
  status-ID cho nhóm `approved` bằng `search_tasks(list, status_type=approved, size=1)` rồi đọc
  `.status` (đã verify khớp đúng). Không tra được (list chưa có task approved) → fallback
  `.env ETASK_RESOLVE_STATUS_REVIEW=<statusId>`; vẫn không có → báo & không đổi.
  `complete_task` (Hoàn thành) là đường chuẩn, không cần status-ID.

### 4b. NOT_FIXED — chưa fix
Thẻ 1: **`👤 Tôi tự làm`** | **`➡️ Giao người khác`**.
- **Tôi tự làm:** nếu đang `todo` → đổi sang `IN_PROGRESS` (`ETASK_RESOLVE_STATUS_INPROGRESS`),
  chỉnh estimate (start = hôm nay, due = +N ngày theo `estimate_days`), thêm comment phân tích.
  Assignee giữ nguyên là **tôi** (đúng ý "tôi nằm ở task để theo dõi").
- **Giao người khác:** thẻ 2 **`✅ Giao <tên>`** | **`❌ Bỏ qua`**. Người gợi ý lấy từ
  `team.py match --exclude <ETASK_MY_LOGIN>` → `team.py get` (đọc `etask_id`). Khi duyệt:
  `assign_task_users(task, [userId], mode=add)` + comment thông tin bổ sung + chỉnh estimate.
  - Thiếu `etask_id` của người đó → tool báo và **không** gán (bạn gán tay / bổ sung team.json).

### 4c. UNCLEAR
Map project không rõ hoặc không đủ chắc để kết luận → chỉ báo Telegram, **không ghi**.

## 5. Chống lấy trùng & gán nhiều người 1 task

- **STATE** `temp/etask_resolved.json`: `{task_id: {statusType, assigned_to}}`. Đã xử lý ở
  đúng `statusType` đó → bỏ qua; lưu `assigned_to` để vòng sau không gán lại người khác.
- **`_inflight`**: task đang chờ duyệt không bị vòng poll kế tiếp bốc lại.
- Trước khi gán: đọc `assignTaskList` từ record SEARCH (⚠️ `get_task` KHÔNG trả assignee — đã verify;
  chỉ record của `search_*` mới có `assignTaskList`/`ownerList`/`assignReviewList`). Nếu đã có người
  khác → cảnh báo trong thẻ xác nhận để bạn quyết, tránh chồng người.
- Tối đa `--max-per-cycle` (mặc định 2) task/vòng + semaphore 2 luồng song song.

## 6. Biến .env liên quan

| Khoá | Mặc định | Ý nghĩa |
|---|---|---|
| `ETASK_RESOLVE_INTERVAL` | 600 | giây giữa các vòng poll |
| `ETASK_RESOLVE_TIMEOUT` | 900 | timeout phân tích `claude -p` mỗi task |
| `ETASK_RESOLVE_STATUS_TYPES` | *(trống = mọi trạng thái chưa completed)* | hẹp phạm vi, vd `todo,processing` |
| `ETASK_RESOLVE_STATUS_INPROGRESS` | *(tự tra)* | **status-ID** fallback cho "đang làm" (nhóm processing) |
| `ETASK_RESOLVE_STATUS_REVIEW` | *(tự tra)* | **status-ID** fallback cho "chờ phê duyệt" (nhóm approved) |
| `ETASK_MY_LOGIN` | `chungtv8` | login của tôi (loại khỏi gợi ý assignee) |
| (dùng lại) `TELEGRAM_APPROVAL_TIMEOUT`, `TELEGRAM_AGENT_MODEL`, `CLAUDE_ACCOUNTS` | | |

## 7. Khác biệt với `etask_watch.py` (triage)

| | `etask_watch.py` (triage) | `task_resolver.py` (resolve) |
|---|---|---|
| Lọc trạng thái | mọi task chưa-done | **chỉ `todo` + `processing`** |
| Xác minh code | không | **có** (mở `work/<project>`) |
| Kết quả | đề xuất EXECUTE/ASSIGN | **đóng** (Hoàn thành/Chờ duyệt) hoặc **giao người + estimate** |
| Gán người | đề xuất (gán tay) | **`assign_task_users` tự động** (sau duyệt) |

## 8. Guardrail

1. Mọi WRITE (đổi trạng thái, gán người, comment, estimate) đều qua **thẻ duyệt Telegram**.
2. Không tự đóng Done khi chưa duyệt; FIXED luôn hỏi Hoàn thành vs Chờ phê duyệt.
3. Không hardcode token/URL — qua `config.py`/`rc_config`.
4. `assign_task_users` mode mặc định `add` (không xoá assignee khác). KHÔNG dùng `replace` tự động.
