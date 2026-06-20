# Setup — Telegram bridge

## 1. Tạo bot
1. Telegram → chat với **@BotFather** → `/newbot` → đặt tên → nhận **token** dạng `123456:ABC...`.
2. Dán vào `.env`: `TELEGRAM_BOT_TOKEN=123456:ABC...`

## 2. Lấy chat_id của bạn
- Cách nhanh: chạy bridge tạm với allowlist rỗng sẽ chặn nhưng báo lại chat_id. Hoặc:
  - Nhắn 1 tin cho bot, rồi: `python tg_api.py me` (xác nhận token), tiếp đó khởi động bridge và
    gửi `/whoami` → bot trả `chat_id`.
- Điền: `TELEGRAM_ALLOWED_CHATS=123456789` (nhiều người: `111,222`).

> Allowlist **fail-closed**: rỗng = chặn tất cả. Chỉ chat_id trong danh sách mới điều khiển được.

## 3. Test wiring
```
cd .claude/skills/remote-control/tools
python tg_api.py test        # gửi tin "kết nối OK" tới mọi allowed chat
```

## 4. Chạy daemon (máy hub luôn-bật — máy Windows này)
```
python telegram_bridge.py
```
- Để cửa sổ chạy. Nhắn `/help` cho bot để xem hướng dẫn.
- Mỗi tin nhắn thường → `claude -p` headless trong repo root (full agent, mọi skill).
- Ngữ cảnh hội thoại giữ qua `--resume` (lưu session theo chat ở `temp/tg_sessions.json`).
  `/reset` để bắt đầu phiên mới.

### Chạy nền / tự bật khi mở máy (Windows)
- Nhanh: `Start-Process python -ArgumentList 'telegram_bridge.py' -WindowStyle Hidden` (PowerShell).
- Bền: tạo Task trong **Task Scheduler** → trigger "At log on" → action chạy
  `python <abs>\telegram_bridge.py`. (Tùy chọn nâng cao, làm khi bạn xác nhận.)

## 5. Dừng
Ctrl+C trong cửa sổ daemon.

## Lưu ý vận hành
- Một lượt agent/chat chạy tuần tự: đang xử lý mà nhắn tiếp → bot báo "đang chạy".
- `claude -p` quá `TELEGRAM_AGENT_TIMEOUT` (mặc định 1800s) → hủy và báo lại.
- Output Telegram tự cắt 4000 ký tự/đoạn; HTML lỗi tag thì tự gửi lại dạng thường.
- `--settings` cấp file hook riêng cho agent bridge. Nếu bản Claude Code của bạn không nhận cờ này,
  xem `approval-flow.md` phần "Phương án dự phòng".  <!-- [Unverified] tên cờ tùy phiên bản CLI -->
