---
name: remote-control
description: Điều khiển agent từ xa qua Telegram và fan-out lệnh ra các máy LAN qua SSH. Telegram bridge chạy headless `claude -p` (full agent, mọi skill) với chế độ DUYỆT bằng nút bấm cho thao tác ghi/SSH/nguy hiểm. Trigger phrases: "telegram bridge", "điều khiển qua telegram", "remote control", "ssh tới máy", "chạy lệnh trên máy LAN", "fan-out ssh", "start bridge", "khởi động bridge".
---

# Remote Control (Telegram + SSH)

Hai mặt phẳng:
- **Control plane — Telegram bridge**: nhắn tin → `claude -p` headless trong repo → trả kết quả. Full agent: mọi skill (auto-dev, etask, gitlab…) dùng được từ điện thoại.
- **Action plane — SSH fan-out**: chạy lệnh trên các máy LAN trong `work/hosts.json` (allowlist, key-based).

**Mô hình tin cậy: full agent + chế độ DUYỆT (cấu hình được).** Mặc định (`TELEGRAM_AUTO_APPROVE=read,file`):
đọc + **đọc/ghi file local → chạy tự do**; lệnh shell ghi (git push, restart, **SSH ra máy khác**, xóa)
→ đẩy nút **✅ Duyệt / ❌ Từ chối** qua Telegram, chờ bạn bấm; lệnh **nguy hiểm** (rm -rf, shutdown,
drop table…) → **LUÔN hỏi**, không bao giờ tự duyệt. Không phản hồi trong `TELEGRAM_APPROVAL_TIMEOUT`
(mặc định 300s) → tự **từ chối**. Muốn toàn quyền không hỏi: `TELEGRAM_AUTO_APPROVE=read,file,bash`.

## Cấu hình (.env)

```
TELEGRAM_BOT_TOKEN=123456:ABC...        # từ @BotFather
TELEGRAM_ALLOWED_CHATS=123456789        # chat_id được phép (cách nhau dấu phẩy). Trống = chặn tất.
TELEGRAM_APPROVAL_TIMEOUT=300           # giây chờ bấm nút trước khi auto-deny
TELEGRAM_AGENT_TIMEOUT=1800             # giây tối đa cho một lượt agent
TELEGRAM_AGENT_MODEL=                   # rỗng = model mặc định; vd 'sonnet' cho nhanh/rẻ
CLAUDE_BIN=                             # rỗng = 'claude' trên PATH
```

> Lấy `chat_id`: nhắn bot bất kỳ, rồi `python tg_api.py me` + xem update, hoặc khởi động bridge rồi
> gửi `/whoami`. Chat chưa có trong allowlist bị chặn và bridge báo lại chat_id để bạn thêm.

## Workflow

### Khởi động bridge (trên máy hub luôn-bật)
1. Điền `.env` (token + allowed chats). Test kết nối: `python tg_api.py test`.
2. Chạy daemon: `python telegram_bridge.py` (Windows: `python`). Để cửa sổ chạy nền.
3. Nhắn `/help` cho bot → kiểm tra thông luồng. Xong: nhắn yêu cầu thật.
4. GUARDRAIL: lần đầu chạy, xác nhận với người dùng trước khi để bridge nhận lệnh ghi.
   Chi tiết: `cookbook/setup.md`.

### SSH fan-out
1. Tạo `work/hosts.json` (mẫu: `hosts.sample.json` trong skill này). Allowlist = chỉ host trong file.
2. Kiểm: `python ssh_exec.py list` · `python ssh_exec.py ping <alias>`.
3. Chạy: `python ssh_exec.py run <alias> "<lệnh>" [--dry-run]`. Phân loại rủi ro tự động
   (`read`/`write`/`danger`) — `danger` luôn cần duyệt khi đi qua bridge.
   Chi tiết: `cookbook/ssh-fanout.md`.

### Form lựa chọn (plan/quyết định) → nút bấm
Agent headless không có giao diện chọn tương tác. Bridge dạy agent (qua `--append-system-prompt`)
phát một khối `[[TG_CHOICE]] … [[/TG_CHOICE]]` khi cần bạn quyết định; `choices.py` parse khối đó
→ render **nút inline bấm được**. Bấm nút → bridge **resume phiên** với phương án đã chọn (như bạn gõ tay).
- Một lượt chỉ một khối lựa chọn (tối đa 8 phương án). Bấm lại nút cũ không kích hoạt lại (idempotent).
- Không có khối → trả lời text bình thường.

### Luồng duyệt (đọc trước khi sửa hook)
- PreToolUse hook `telegram_approve.py` CHỈ kích hoạt khi `CLAUDE_TG_BRIDGE=1` (bridge tự đặt khi
  spawn agent). Phiên Claude Code tương tác của bạn KHÔNG bị ảnh hưởng.
  Chi tiết: `cookbook/approval-flow.md`.

## Tools (`.claude/skills/remote-control/tools/`)

```
python tg_api.py me|test|send <chat> "text"        # Telegram Bot API (kiểm tra wiring)
python telegram_bridge.py [--test]                 # daemon: Telegram -> claude -p (full agent)
python telegram_approve.py                          # PreToolUse hook (bridge gọi, không gọi tay)
python ssh_exec.py list|ping <a>|run <a> "<cmd>" [--dry-run]|classify "<cmd>"
```

## Guardrail (BẮT BUỘC)

1. **Allowlist là bắt buộc.** `TELEGRAM_ALLOWED_CHATS` rỗng → bridge chặn tất (fail-closed).
   `work/hosts.json` rỗng → không SSH được tới đâu.
2. **Lệnh shell ghi / SSH ra máy khác / nguy hiểm phải được DUYỆT** bằng nút Telegram (đọc & ghi file
   local thì tự chạy theo `TELEGRAM_AUTO_APPROVE`). Hook fail-safe: Telegram lỗi/timeout → **từ chối**.
   `danger` luôn hỏi dù cấu hình thế nào.
3. **Lệnh `danger`** (rm -rf, mkfs, shutdown, drop table…) bị gắn cờ ⚠️ trong thẻ duyệt — đọc kỹ
   trước khi bấm.
4. **Không hardcode token/host** — qua `.env` và `work/hosts.json`.
5. Bridge để máy hub trở thành cửa điều khiển từ xa — coi token Telegram như mật khẩu, đừng commit.
