# Plan hoàn thiện toolkit — Vòng lặp học từ chỉnh sửa của con người

> Trạng thái: **đang thực hiện** · Nguồn: audit toàn bộ toolkit 2026-07-05 · Cập nhật tiến độ 2026-07-05
> Nguyên tắc thiết kế: giữ human-in-the-loop, nhưng **biến mỗi lần con người can thiệp thành dữ liệu học** —
> càng chạy lâu, tỉ lệ "duyệt thẳng không cần sửa" càng tăng, mức tự động được nâng dần bằng số liệu.
>
> **Đã làm (2026-07-05):** Phase A (làm giàu Intake — mới, ngoài plan gốc) + lõi Phase 1/2/4
> (Feedback Ledger + Recall + stats). Xem mục "Phase A" và các ô ✅ bên dưới. Còn lại: tự động
> harvest (1.2–1.4), Distill (Phase 3), digest + graduated autonomy (4.2–4.3), vá daemon (Phase 0).

## Kiến trúc vòng lặp

```
Agent đề xuất (plan / review / triage / notify)
        │
        ▼
Bạn duyệt / sửa / bác  ──────────────►  Feedback Ledger (ghi lại TẤT CẢ can thiệp)
   (checkpoint, Telegram)                        │
        ▲                                        ▼
        │                          Recall (bơm correction cũ vào prompt lần sau)
        │                                        │
        └──── Quy tắc chưng cất ◄─── Distill (10+ record mới → cập nhật conventions)
              (bạn duyệt bản chưng cất)
```

Điều kiện nền phát hiện từ audit: hiện **không có chỗ nào ghi lại quyết định của con người**
(approval files bị xoá sau khi quyết; checkpoint chỉ lưu approved/rejected, không lưu *đã sửa gì*).
Vì vậy Phase 1 là bắt buộc trước mọi thứ khác — không có dữ liệu thì không có gì để học.

---

## Phase A — Làm giàu Intake ✅ ĐÃ LÀM (mới, ngoài plan gốc)

Chẩn đoán bổ sung: độ chính xác auto thấp không chỉ vì task mơ hồ, mà vì pipeline **chỉ đọc
`title`+`description`** rồi lập plan — bỏ phí ngữ cảnh đã có (comment/checklist/subtask, code repo),
và lập plan khi còn "mù repo". Bốn đòn bẩy đã triển khai + test trên task eTask thật:

- [x] **A.1 `context_pack.py`** (`auto-dev/tools/`) — gom description + **comment + checklist + subtask**
      (eTask) hoặc **AC/root-cause/solution** (Azure) → `temp/runs/<src>-<id>_context.md`; xuất `ac_seeds`
      + `keywords` sạch + `signals.thin_description`. Dùng làm `--desc-file` cho mọi bước sau.
- [x] **A.2 `grounding.py scout`** — pre-grounding: grep `clone_dir` theo từ khoá task → file ứng viên
      (`temp/runs/<RID>_scout.md`) **trước** khi plan, để plan neo vào code thật. Có lọc ID nhiễu.
- [x] **A.3 `clarify.py` đề xuất câu trả lời** — thêm `--context-file` (scout+pack) và trường `proposed`
      (câu trả lời cụ thể có căn cứ, fallback về assumption). Người chỉ xác nhận/sửa → giảm ma sát.
- [x] **A.4 Ghép triage↔clarity** — `triage.py --clarity`: `mode=auto` chỉ khi `tier=trivial` VÀ
      `clarity=pass`; task mơ hồ bị ép hạ auto→checkpoint (`clarity_downgrade`). Khuyến nghị `--backend`
      mặc định cho task thật.
- [x] **A.5 Wire pipeline** — sắp lại Intake: context_pack → scout → recall → clarify → triage; cập nhật
      `SKILL.md` + `cookbook/intake.md` + `cookbook/pipeline.md`.

> [Inference] Kỳ vọng: input vào debate/plan "no thông tin" hơn → ít plan sai file/scope hơn. Sẽ đo
> bằng `feedback.py stats` (Phase 4) khi có dữ liệu thật.

---

## Phase B — Intake Queue: xử lý TUẦN TỰ ✅ ĐÃ LÀM (2026-07-05, ngoài plan gốc)

Yêu cầu mới: đọc task từ eTask → làm giàu + làm rõ yêu cầu → xếp hàng đợi; **không chạy
đồng thời** (nguy cơ xung đột code khi nhiều task chạy cùng repo).

- [x] **B.1 `task_queue.py`** (`auto-dev/tools/`) — `scan` (task eTask chưa vào queue) ·
      `intake` (context_pack → scout → recall → clarify, lưu artifact vào item) · `answer`
      (`--accept-proposed` one-click; **mặc định sync brief lên eTask** [WRITE] để bàn giao
      được cho người khác, `--no-sync` để bỏ) · `next`/`done` (khoá TUẦN TỰ **theo luồng** —
      owner mặc định `task_resolver`, người làm tay không bị chặn) · `release`/`requeue`/`remove`.
      Item ở `work/queue/items/<qid>.json`, lock ở `lock_<owner>.json`.
- [x] **B.1b Wire `task_resolver.py`** — daemon claim/release CÙNG flow lock quanh mỗi task
      (`try_claim`/`release_claim`); lock bận → task để lại vòng poll sau. Resolver + queue
      không bao giờ chạy 2 task tự động cùng lúc trên repo.
- [x] **B.2 Thin guard** — mô tả rỗng + không comment/checklist/subtask → ép
      `needs_clarification` (heuristic clarify bị khung markdown của pack che mắt; phát hiện
      qua test intake task eTask thật).
- [x] **B.3 Tests + docs** — 18 unit test mới (71/71 xanh); `cookbook/intake.md` § Queue,
      `SKILL.md` tool row, slash command `/etask-queue`. Đã verify sống: `scan` (20 task),
      `intake` task thật (pack + scout trên clone etask, clarify heuristic).
- [ ] **B.4 (sau)** — worker daemon tự `next` khi rảnh (hiện `next` do người/agent gọi);
      gộp `etask_watch` enqueue thay vì spawn thẳng.

---

## Phase C — Gate VERIFY: chạy thật → soi output → soi DB ✅ ĐÃ LÀM (2026-07-06)

Vấn đề: pipeline có đủ tool runtime (local_app/probe_*/flow_check) nhưng gate `integration`
chỉ advisory + không ai sinh kịch bản → task lên MR chỉ với unit test, chưa từng chạy code
thật và soi dữ liệu. Đã đóng 3 khớp thiếu:

- [x] **C.1 `verify_gen.py`** (`auto-dev/tools/`) — plan + AC ledger → `temp/runs/<RID>_verify.json`
      (flow_check format; mỗi AC hành-vi/dữ-liệu = 1 step `"ACn: ..."`); trả `touches_runtime`
      (heuristic controller/service/repository/sql/migration) + `acs_uncovered` + `needs_review`.
      Người duyệt kịch bản CÙNG plan ở mốc `after_plan`.
- [x] **C.2 `run_log.py require <RID> verify`** — nâng gate theo run: `touches_runtime` → verify
      BẮT BUỘC trên stage test (auto mode chặn advance khi chưa pass); task docs/refactor không bị ép.
      `ac-map --verify-json v.json`: AC chỉ được "met" khi step `"ACn: ..."` ĐÃ PASS thật —
      từ chối step fail/thiếu (hết thời "unit test passed" đóng AC dữ liệu).
- [x] **C.3 `spring_config.py`** (`dev-automation/tools/`) — parse `application-<env>.yml`
      (YAML subset stdlib, chuẩn + JHipster `resources/config/`, `${VAR:default}`, multi-doc
      profile) → engine/host/port/db/schema/user/password + server.port/base_url.
      `project_config.resolve` tự **gap-fill** DB từ Spring khi registry thiếu (registry thắng
      từng khoá) → probe/flow_check hit đúng DB app đang dùng, không cần khai lại.
- [x] **C.4 Tests + docs** — 19 test mới (95/95 xanh); verify sống trên repo etask thật
      (JHipster: mysql `idaas_etask@10.14.121.8`, port 8271). Docs: pipeline.md §2 (sinh+require)
      + §4 (đường verify chính thức) + §5 (`ac-map --verify-json`), SKILL.md, stack-verify.md.
- [x] **C.5 Đầu-ra-của-task = API bị ảnh hưởng** ✅ (2026-07-06) — `verify_gen --root <clone_dir>`:
      bóc endpoint từ controller trong `<target_files>` (regex `@*Mapping` + prefix cấp class;
      sửa `XxxService` → tự dò `XxxController`/`XxxResource` cùng tên) → đưa vào prompt
      "PHẢI gọi các API này" + cảnh báo `endpoints_untested` khi kịch bản bỏ sót.
      Verify sống: bóc đúng 24 endpoint thật từ `TaskResource.java` của etask.
- [x] **C.6 `app_run_cmd` trong registry** ✅ (2026-07-06) — `local_app.py start` ưu tiên
      `--cmd` > `projects.json.app_run_cmd` > mvn default; etask đã khai lệnh
      java+PropertiesLauncher (2 ngõ cụt mvnw/exec:java ghi ở `_app_run_note` + memory).
      Verify sống: start không cần `--cmd` → app UP → stop sạch.
- [ ] **C.7 (sau)** — nối verify fail → feedback ledger (tag `missed-verify`); sinh scenario
      cho Kafka/Redis khi plan nhắc tới; verify chạy dưới `bg_notify` khi qua Telegram bridge.

---

## Phase 0 — Vá nền vận hành (~2–3 ngày)

Mục tiêu: daemon sống đủ lâu để sinh dữ liệu. Làm trước vì mọi phase sau phụ thuộc.

- [x] **0.1 Supervisor loop + exponential backoff** ✅ (2026-07-05)
      - `daemon_common.py` (`dev-automation/tools/`): `supervise()` + `Backoff` (1s→×1.5→trần 60s,
        reset khi thành công) + `guard()`/`classify_status()` + `DaemonFatal`/`DaemonTransient`.
      - **REST poller** (`mr_watch`, `etask_watch`): loop `sleep(interval)` cố định → thay bằng
        `supervise`; `401/403` = **dừng + báo** (etask alert Telegram), `0/429/5xx`/crash = backoff.
      - **WS listener** (`group_watch`, `task_digest`): vốn đã có reconnect-backoff + `tokens.refresh`
        → thêm health_log + fatal-log khi token chết (không rewrite, tránh rủi ro).
      - **`telegram_bridge`**: vòng getUpdates `sleep(3)` cố định → backoff + phân biệt code 401/403.
- [x] **0.2 Health log** ✅: `temp/daemon_health.jsonl` (`started`/`transient`/`fatal`/`recovered`/
      `stopped`) + `python daemon_common.py status` (last event per daemon). Đã smoke-test.
- [ ] **0.3 Sửa mâu thuẫn auto-post review** (doc nói hỏi, code tự post):
      - Chọn hành vi: `mr_watch` **tự post** (bạn đã opt-in khi bật watcher) · `/review-mr` chạy tay **hỏi trước**.
      - Sửa đồng bộ `mr_watch.py` (context ~dòng 173–179) và `watch-mrs.md` (dòng 15).
- [ ] **0.4 Thu hẹp auto-approve**: `TELEGRAM_AUTO_APPROVE=read,file` (bỏ `bash` — git push/ssh/restart
      phải qua nút duyệt). Sửa `.env` + `.env.sample` + docs remote-control.

**Nghiệm thu:** ngắt mạng / để token hết hạn → daemon không chết, ghi log, tự hồi khi có lại;
`daemon_status.py` báo đúng trạng thái từng daemon.

---

## Phase 1 — Feedback Ledger: ghi lại mọi can thiệp ⭐ lõi (~3–4 ngày)

Tool mới dùng chung: `dev-automation/tools/feedback.py` (stdlib-only). ✅ **ĐÃ TẠO.**
Lưu trữ: `work/feedback/<project>.jsonl` — append-only, **không bao giờ xoá**, gitignored. ✅

### Schema record

```json
{"ts": "2026-07-05T10:00:00Z", "run_id": "etask-123", "project": "etask",
 "stage": "plan", "task_type": "bugfix", "tier": "standard",
 "agent_output": "<tóm tắt/hash đề xuất của agent>",
 "human_action": "edited | approved | rejected",
 "correction": "<diff hoặc mô tả đã sửa gì>",
 "reason": "<1 câu tại sao — trường quan trọng nhất>",
 "tags": ["convention", "wrong-file", "missed-ac", "style"]}
```

### Điểm móc thu thập (khớp quy trình hiện có)

- [~] **1.1 Checkpoint của run_log** — ĐÃ có hướng dẫn bắt buộc ghi `feedback.py add --action edited`
      khi plan bị sửa ở mốc `after_plan` (cập nhật trong `SKILL.md` + `pipeline.md`). **CÒN LẠI:** chưa
      mở rộng `run_log.py checkpoint` để *ép* (nhắc khi checkpoint có sửa mà thiếu record).
- [ ] **1.2 Telegram approvals** — `approvals.py` hiện xoá file sau khi quyết → đổi thành
      chuyển vào ledger (approve/deny + note gõ kèm nút). *(chưa làm)*
- [ ] **1.3 Harvest MR review** — `feedback.py harvest-mr <iid>`: sau khi MR đóng, so review của
      agent với thực tế (comment người thật thêm + commit sửa sau review = những gì agent bỏ sót)
      → record `stage: review`. Gọi định kỳ từ vòng poll của `mr_watch`. *(chưa làm)*
- [~] **1.4 Triage bị đổi tay** — `feedback.py add --stage triage --action overridden` đã hỗ trợ +
      ghi trong docs. **CÒN LẠI:** chưa tự động hook khi phát hiện override.
- [x] **1.5 CLI cơ bản**: `feedback.py add | list | search --stage --project --tags --limit` ✅
      (thêm cả `recall` + `stats`).

**Nghiệm thu:** chạy 1 task qua pipeline, sửa plan 1 lần + deny 1 approval →
`feedback.py list` ra đúng 2 record có `reason`.

---

## Phase 2 — Recall: bơm lịch sử vào lần chạy sau (~2–3 ngày)

- [x] **2.1** `feedback.py recall --stage plan --project etask --type bugfix --limit 5` ✅
      → correction liên quan nhất; lọc project → stage → task_type; chấm điểm keyword overlap + tag,
      mới nhất ưu tiên (stdlib). Xuất luôn khối `<past_corrections>` sẵn để chèn.
- [~] **2.2** Nối vào điểm tiêu thụ:
      - [x] `debate_engine.py --corrections-file`: chèn `<past_corrections>` vào mô tả task cho cả 3 vai ✅
      - [ ] `review_gate.py` + context của `mr_watch`: lỗi review từng bị bỏ sót *(chưa làm)*
      - [ ] `triage.py --backend`: các lần tier bị đổi tay *(chưa làm)*
- [x] **2.3** Giới hạn block corrections (`RECALL_BLOCK_BUDGET=1500` ký tự, mới nhất/liên quan trước) ✅

**Nghiệm thu:** ghi tay 1 record "đừng dùng field X, dự án này dùng Y" → chạy debate task
tương tự → block corrections xuất hiện trong prompt và plan phản ánh nó.

---

## Phase 3 — Distill: chưng cất lịch sử thành quy tắc bền (~2–3 ngày)

Raw records sẽ phình và nhiễu → chưng cất định kỳ thành quy tắc khái quát.

- [ ] **3.1** `feedback.py distill --project <P>`: khi có ≥10 record mới, spawn `claude -p` đọc
      records → đề xuất cập nhật `work/conventions/<project>.md`
      (vd: "controller layer dự án này không throw raw exception",
      "AC dạng báo cáo luôn cần test số liệu biên").
- [ ] **3.2** **Bản chưng cất phải qua người duyệt**: gửi diff qua Telegram với nút Approve/Edit —
      human-in-the-loop áp cho cả việc học, tránh học sai từ correction nhiễu.
- [ ] **3.3** `grounding.py` + `review_gate.py` nạp `conventions/<project>.md` mặc định
      → quy tắc sống lâu dài, không tốn recall.
- [ ] **3.4** Trigger: đếm record mới trong vòng poll của `etask_watch`/`mr_watch`,
      hoặc chạy tay hàng tuần.

---

## Phase 4 — Đo "càng chạy càng chính xác" (~2 ngày)

Không đo thì không biết vòng lặp có hoạt động.

- [~] **4.1** `feedback.py stats` ✅ (bản đầu): **tỉ lệ duyệt-thẳng-không-sửa theo stage** (chỉ số chính)
      + phân bố tags lỗi + đếm corrections/stage. **CÒN LẠI:** số vòng retry test, tỉ lệ review bị bác,
      và cắt theo tuần/tháng.
- [ ] **4.2** Digest tuần đẩy qua Telegram (tái dùng `bg_notify` / `tg_api`).
- [ ] **4.3 Graduated autonomy** — hoà giải human-in-loop ↔ tự động:
      - Lát cắt (vd `etask + bugfix + tier=standard`) có tỉ lệ duyệt-thẳng ≥ 90% trên 20 lần
        gần nhất → hệ thống **đề xuất** nâng lên `mode=auto`, bạn xác nhận.
      - Lát bị sửa nhiều → tự hạ về `checkpoint`.
      - Mức tin cậy **kiếm bằng dữ liệu**, không đặt cứng.

> [Inference] Kỳ vọng độ chính xác tăng dần là suy luận từ thiết kế (nhiều ngữ cảnh đúng hơn
> trong prompt → ít lỗi lặp lại hơn), không bảo đảm trước — chỉ số ở phase này là công cụ kiểm chứng.

---

## Phase 5 — Giảm ma sát duyệt + trả nợ drift (~3–4 ngày, song song được)

- [ ] **5.1 Thẻ duyệt Telegram cho checkpoint**: 1 card/checkpoint gồm artifact tóm tắt + nút
      `✅ Duyệt / ✏️ Sửa (gõ note) / ❌ Bác` — duyệt từ điện thoại; mỗi lần bấm = 1 feedback record
      (Phase 1 hưởng lợi tự nhiên). Checkpoint không mất đi, chỉ rẻ hơn.
- [ ] **5.2 Trả nợ docs-vs-code** (từ audit):
      - team-registry: chọn **wire thật** (`etask_watch` gọi trực tiếp `team.py match`)
        hoặc **xoá claim** ở `team-registry/SKILL.md:43-46`.
      - Nhánh ASSIGN của `etask_watch`: docs nói rõ "chỉ gợi ý, không assign thật" (API limit).
      - `etask SKILL.md:192` vs `task_resolver.py` (claim dùng search_tasks nhưng không import).
      - Viết mục "chọn search tool nào" (search vs governed_search vs analytics) vào cookbook etask.
- [ ] **5.3 Dọn dẹp**: GC `temp/mr_reviewed.json` (xoá entry MR đã đóng >30 ngày);
      xác nhận số phận `bg_notify.py`, `doctor.py`, `kafka_ui.py` (dùng thật → ghi docs, không → xoá).

---

## Thứ tự thi công & lý do

**A ✅ → (1 lõi ✅ · 2 lõi ✅ · 4 lõi ✅) → còn: 1.2–1.4 · 2.2 (review/triage) · 3 · 4.2–4.3 · 0**

| Bước | Trạng thái | Lý do đứng ở vị trí này |
|---|---|---|
| Phase A | ✅ đã làm | Làm giàu Intake — vá trần chất lượng ngay tại đầu vào (mới, ngoài plan gốc) |
| Phase 0 | ⬜ chưa | Hệ thống phải sống đủ lâu mới sinh được dữ liệu |
| Phase 1 | 🟡 lõi xong | Ledger + CLI add/list/search chạy được; còn tự động harvest (1.2–1.4) |
| Phase 2 | 🟡 lõi xong | recall + wire debate xong; còn review_gate/triage |
| Phase 4 | 🟡 lõi xong | stats bản đầu (straight-through rate + tags); còn digest + graduated autonomy |
| Phase 3 | ⬜ chưa | Chưng cất khi đã đủ record và đã có thước đo |
| Phase 5 | ⬜ chưa | Chen vào lúc rảnh, không chặn vòng lặp học |

Tổng ước lượng còn lại: **~1.5–2 tuần** (tự động harvest + Distill + graduated autonomy + vá daemon).
Vòng lõi (Phase A + lõi 1/2/4) **đã chạy được bằng tay ngay**.

## Rủi ro chấp nhận ở v1

1. Recall bằng keyword có thể nhặt correction không liên quan → chấp nhận, dùng `tags` lọc dần;
   nâng cấp scoring khi thấy nhiễu thật.
2. Ledger nằm ở `work/` (gitignored) = dữ liệu máy-cụ-thể → cần backup/sync nếu làm trên nhiều máy.
3. Distill bằng LLM có thể khái quát sai → chặn bằng bước duyệt diff (3.2), không auto-merge.
