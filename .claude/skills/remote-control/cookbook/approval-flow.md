# Luồng duyệt (approval) qua Telegram

## Ý tưởng
Agent chạy headless không có người ngồi trước máy. Để vẫn an toàn, mọi thao tác **ghi / SSH /
nguy hiểm** bị một **PreToolUse hook** chặn lại, đẩy nút bấm sang Telegram và **chờ** bạn quyết.

```
claude -p (CLAUDE_TG_BRIDGE=1)
   │  định gọi tool (Bash/Edit/Write/...)
   ▼
PreToolUse hook  telegram_approve.py
   │  phân loại rủi ro
   ├─ read-only ───────────────► allow (không hỏi)
   └─ write / ssh / danger
        │  approvals.create() → temp/tg_approvals/<id>.json
        │  tg_api.send_message(nút ✅/❌)
        │  approvals.wait(<id>, timeout)  ⟵ chặn ở đây
        ▼
telegram_bridge.py (vòng poll vẫn chạy)
        │  nhận callback_query khi bạn bấm
        │  approvals.decide(<id>, yes/no)
        ▼
hook nhận verdict → permissionDecision: allow | deny
```

Điểm mấu chốt: **bridge và hook là hai tiến trình khác nhau**, chia sẻ qua thư mục
`temp/tg_approvals/`. Bridge phải tiếp tục poll Telegram trong khi `claude -p` (và hook bên trong nó)
đang chờ — nên agent chạy ở **thread riêng**, vòng poll chính không bị khóa.

## Quy tắc phân loại (telegram_approve.py)
- **allow ngay**: `Read, Grep, Glob, LS, NotebookRead, WebFetch, WebSearch, TodoWrite`, và `Bash`
  mà `ssh_exec.classify` cho là `read` (ls, cat, df, git status, docker ps, kubectl get, …).
- **hỏi duyệt**: `Edit, Write, MultiEdit, NotebookEdit`, `Task` (spawn agent), và `Bash` `write`/`danger`.
- **mặc định**: tool lạ → hỏi (fail-safe).
- Lệnh `danger` (rm -rf, mkfs, dd, shutdown, drop table, …) gắn cờ ⚠️ trên thẻ duyệt.

## Fail-safe
- Telegram gửi lỗi, không có `CLAUDE_TG_CHAT_ID`, hoặc hết `TELEGRAM_APPROVAL_TIMEOUT` giây không bấm
  → **deny**. Không bao giờ chạy khi chưa được duyệt rõ ràng.

## Vì sao không ảnh hưởng phiên tương tác
Hook kiểm `CLAUDE_TG_BRIDGE == "1"` đầu tiên. Không phải agent do bridge spawn → hook `exit 0`
im lặng, Claude Code đi tiếp luồng quyền bình thường. Đã test: stdin một lệnh `rm -rf /` mà không có
biến môi trường → không in gì, exit 0.

## Phương án dự phòng nếu `--settings` không có trên bản CLI của bạn
Bridge gắn hook bằng cờ `--settings <file>` khi spawn agent. Nếu phiên bản Claude Code không nhận:
1. Đăng ký hook ở `.claude/settings.json` của repo (PreToolUse, matcher `*`, command trỏ tới
   `telegram_approve.py`). An toàn vì hook tự no-op khi không ở chế độ bridge.
2. Hoặc dùng `--permission-prompt-tool` (MCP) nếu bạn đã có. <!-- [Unverified] tùy phiên bản -->

Kiểm cờ thực tế: `claude --help | grep -i settings`.
