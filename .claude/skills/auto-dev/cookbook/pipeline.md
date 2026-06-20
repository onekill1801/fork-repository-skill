# Auto-Dev Pipeline — chi tiết câu lệnh

Pipeline đầy đủ cho một task: **Intake → (Triage) → Plan → Implement → Test → Deliver**,
**autonomy Hybrid theo độ phức tạp**. Mọi tool ở `.claude/skills/dev-automation/tools/` (lõi
state) và `.claude/skills/auto-dev/tools/` (triage / grounding / review).

Quy ước trong tài liệu này:
- `<RID>` = run_id, gợi ý `<project>-<task_id>` (vd `etask-12345`).
- `<P>` = tên project trong `./work/projects.json`.
- Đặt env inline mỗi lệnh GitLab/Azure khi làm đa project (xem `dev-automation/cookbook/multi-project.md`).

> **Nguyên tắc gate (cốt lõi):** một stage chỉ được `done` qua `run_log.py advance` khi đã có
> **bằng chứng** (gate-result `pass`/`waived`) cho stage đó — KHÔNG dùng `stage <RID> <s> done`
> để tự khai trong mạch tự động (lệnh `stage` vẫn còn để gỡ tay/tương thích ngược).
> `advance` đọc `mode` của run: **auto** → gate hỏng = CHẶN; **checkpoint** → gate hỏng = báo cho
> người duyệt (không chặn). Đó là toàn bộ khác biệt giữa hai mức autonomy.

---

## 0. Triage + khởi tạo run-log

Triage phân loại task để chọn tier/mode (chi tiết nguồn intake: `cookbook/intake.md`):

```bash
cd .claude/skills/auto-dev/tools
python triage.py classify --type bugfix --title "<tiêu đề>" --desc "<mô tả>"
# -> {tier, mode, reason, skip_debate}. Heuristic mặc định; thêm --backend claude khi mơ hồ.
```

Khởi tạo run-log với tier/mode lấy từ triage:

```bash
cd ../../dev-automation/tools
python run_log.py init <RID> --task <task_id> --project <P> --type bugfix --title "<tiêu đề>" \
    --tier <trivial|standard|complex> --mode <auto|checkpoint>
```

- `trivial` → `--mode auto`, **được phép skip debate** và `advance` tự đi nếu gate xanh.
- `standard|complex` → `--mode checkpoint` (mặc định an toàn), giữ 3 mốc duyệt.
- Trích **acceptance criteria** từ task vào sổ AC (đối chiếu ở Deliver):
  `python run_log.py ac-add <RID> --text "<tiêu chí>"` (lặp cho từng tiêu chí).

Nếu chưa có `./work/projects.json`: hỏi người dùng đường dẫn thư mục code (`clone_dir`) và
lệnh test, rồi truyền trực tiếp qua `--cwd` / `--cmd` ở bước Test.

## 1. Intake — đọc task

Azure: `python azure_devops.py get <task_id>` · eTask: xem `cookbook/intake.md`.
Trích: tiêu đề, mô tả, repro/acceptance, severity, work item liên quan.

## 2. Plan — qua Agent Debate (tranh biện trước khi code)

```bash
python run_log.py stage <RID> plan active
```

> **Skip debate cho `tier=trivial`:** task tầm thường (1 file, rủi ro thấp) thì debate là overkill.
> Khi `skip_debate=true` từ triage: bỏ qua debate, viết spec gọn thẳng vào
> `temp/runs/<task_id>_plan.xml` (vẫn cần `<target_files>` để bước grounding bóc tách), rồi
> `run_log.py stage <RID> plan done`. `plan` không có required-gate nên `advance <RID> plan` đi
> thẳng. Với `standard|complex` → chạy đủ debate dưới đây.

Thay vì để 1 agent viết thẳng plan, chạy **Agent Debate Engine** ở đầu tầng Plan: ba vai (Dev /
Architect / Moderator) tranh biện để thẩm định kiến trúc, soi lỗ hổng bảo mật (SQLi, rate-limit,
connection pool, memory leak, thiếu cache) và tối ưu hiệu năng trước khi đụng code thật.

```bash
cd ../../auto-dev/tools
# Mặc định gọi CLI agent bản subscription → KHÔNG cần ANTHROPIC_API_KEY:
python debate_engine.py run --task <task_id> --desc "<mô tả task>"            # backend=claude (đã chạy thật OK)
python debate_engine.py run --task <task_id> --desc "..." --backend cursor     # Cursor CLI (binary `agent`)
python debate_engine.py run --task <task_id> --desc "..." --backend agy         # Antigravity
# Người dùng xem cuộc tranh biện (có màu) trên STDERR; STDOUT là JSON {spec_path, ...}.
# Kết quả: temp/runs/<task_id>_plan.xml chứa <final_specification> đã được phản biện.
```
Backend (cờ đã đối chiếu `--help` thực tế; tất cả qua subscription, KHÔNG cần API key):
- `claude` (mặc định) — `claude -p --model <m> --dangerously-skip-permissions`. **✅ Đã chạy thật, 4 vòng OK.**
- `cursor` — Cursor CLI, binary tên **`agent`**: `agent -p --output-format text --model <m> --force --trust`.
  Cờ khớp `agent --help` (print mode thiết kế cho script/non-interactive). Nên chạy được headless;
  xác nhận trên máy bạn (binary `agent` ở PATH cmd của bạn).
- `agy` — Antigravity: `agy --model <m> --print --dangerously-skip-permissions`. ⚠️ **[chưa chạy được
  headless ở đây]**: probe thực tế bị treo/timeout, `agy models` in stdout rỗng → agy có vẻ ghi ra
  TUI/kênh khác khi không có TTY. Nếu treo: dùng `claude`/`cursor`, hoặc `--cmd-template`.
- CLI khác / binary tên khác → `--backend custom --cmd-template "<lệnh> {model}"`.
- Chỉ `--backend api` mới cần `ANTHROPIC_API_KEY`. `--dry-run` = mock, không gọi gì.
- `<final_specification>` là dữ liệu Agent↔Agent (thẻ HTML/XML, KHÔNG Markdown — xem
  `auto-dev/prompts/SYSTEM_PROMPT.md`); bên trong có `<approach>`, `<target_files>`, `<test_strategy>`.

Khi bước Implement cần danh sách file cần sửa, **KHÔNG cắt chuỗi Markdown theo dòng** — bóc tách
cấu trúc từ file spec bằng parser chuẩn (`--file` đặt SAU tên lệnh con):

```bash
# Danh sách file cần sửa:
python ../../fork-terminal/tools/agent_parser.py list target_files file --file ../../../../temp/runs/<task_id>_plan.xml
# Khối approach / test_strategy:
python ../../fork-terminal/tools/agent_parser.py tag approach --file ../../../../temp/runs/<task_id>_plan.xml
```

> Worktree của Agent phụ (fork ra qua `fork-terminal`) đọc CHÍNH `temp/runs/<task_id>_plan.xml`
> này để lập trình — không cần truyền plan qua kênh khác.

```bash
python run_log.py stage <RID> plan done
```

### ✋ Checkpoint `after_plan`
Trình bày plan cho người dùng **dưới dạng Markdown sạch** (bỏ thẻ — thẻ chỉ dùng cho
Agent↔Agent). Chờ duyệt. Khi được duyệt:
```bash
python run_log.py checkpoint <RID> after_plan approved
```
KHÔNG sửa code trước khi mốc này được duyệt.

## 3. Implement

> **Nhánh đích theo env (branch-per-env):** base branch + MR target = nhánh của env đang làm,
> lấy bằng `project_config.target_branch(<P>, <env>)` (vd dev→`dev`, uat→`uat`, prod→`prod`,
> sandbox→`pre-prod`; không có env → `default_target_branch`). Đặt `<TB>` = nhánh đó.
> Branch off đúng `<TB>` (checkout/pull `<TB>` trong `clone_dir` trước khi tạo nhánh mới).

```bash
python run_log.py stage <RID> implement active
TB=$(python -c "import project_config as p; print(p.target_branch('<P>','<env>'))")
GITLAB_PROJECT_ID=<id> python gitlab_api.py create-branch "bugfix/<task_id>-<short>" "$TB"
python run_log.py field <RID> branch "bugfix/<task_id>-<short>"
```

**Grounding (gate bắt buộc của Implement)** — đọc code thật trước khi sửa, tránh code mù lệch
convention. Gom `<target_files>` + file lân cận + stack hint thành artifact cho agent implement:

```bash
cd ../../auto-dev/tools
python grounding.py run --run <RID> --root "<clone_dir>" > ../../../../temp/runs/<RID>_grounding.json
#   --backend claude (tuỳ chọn) để agent tóm tắt convention vào artifact.
cd ../../dev-automation/tools
python run_log.py record-gate <RID> grounding --verdict pass --json ../../../../temp/runs/<RID>_grounding.json
```
> `grounding` trả `verdict:"fail"` khi KHÔNG định vị được file nào trong plan (sai path/sai
> checkout) → ở `mode=auto` sẽ chặn Implement (đúng: không nên code khi plan không khớp repo).

- Viết code trong `clone_dir`, đọc `temp/runs/<RID>_grounding.md`, theo `java-standards.md`.
- **Thêm/chỉnh test** chứng minh thay đổi (bug: test fail trước fix, pass sau fix).
- Commit + push trong thư mục project.

```bash
python run_log.py advance <RID> implement      # qua được iff gate 'grounding' pass/waived
```

## 4. Test (cổng bắt buộc)

```bash
python run_log.py stage <RID> test active
# Unit test (bắt buộc):
python test_runner.py run --project <P> --kind test  > t.json
python run_log.py record-gate <RID> test --json t.json    # verdict suy từ "passed" trong t.json
# Lint (BẮT BUỘC, waivable): project có linter -> record verdict theo kết quả; không có linter ->
python test_runner.py run --project <P> --kind lint  > l.json
python run_log.py record-gate <RID> lint --json l.json        # hoặc: --verdict waived --summary "no linter"
# Build (advisory — không tự chặn):
python test_runner.py run --project <P> --kind build > b.json
python run_log.py record-gate <RID> build --json b.json
```
`record-gate --json` tự suy verdict từ `{passed/timed_out/error}` của test_runner.

Xử lý fail (cổng `test`):
- `test` đỏ → JSON kèm `error_context` (thẻ `<error_context>...</error_context>`). Đưa NGUYÊN thẻ
  cho Fix-agent, sửa nguyên nhân, chạy lại + `record-gate` lại. Lặp tối đa **3 lần**. Hết 3 lần:
  ```bash
  python run_log.py note <RID> "test still red after 3 retries: <tóm tắt>"
  ```
  → DỪNG, báo người dùng, KHÔNG `advance`, KHÔNG tạo MR.

Chốt cổng Test:
```bash
python run_log.py advance <RID> test    # auto: chặn nếu test|lint chưa pass/waived; checkpoint: báo + đi tiếp
```

Khi chưa có registry, thay `--project <P>` bằng `--cwd <dir> --cmd "<lệnh test>"`
(hoặc `--cwd <dir> --auto` để tự dò pom.xml/package.json/...).

**Cổng integration/e2e** (khi task chạm DB/API/Kafka/Redis) — chi tiết: `cookbook/stack-verify.md`.
Chạy ở env **non-prod** (dev/uat/sandbox) qua `--project <P> --env <env>`:

> **Trước probe DB trên worktree đã isolate:** `runtime_isolator` chỉ ĐỔI TÊN DB (`<db>_task_<id>`),
> KHÔNG tạo DB. Chạy guard xác nhận đang ở đúng DB đã isolate (và nó tồn tại) — nếu sai/không có,
> guard trả `error` (= chưa kiểm được, KHÔNG phải pass):
> ```bash
> python probe_db.py check-db --engine postgres --project <P> --env dev --expect-db "<db>_task_<task_id>"
> ```
> Tên `<db>_task_<task_id>` lấy từ `runtime_isolator.isolated_db_name(<db>, <task_id>)`.

```bash
python flow_check.py --file ../../auto-dev/scenarios/<scenario>.json --project <P> --env dev
# hoặc probe lẻ cho phần thay đổi:
python probe_api.py call --url /api/... --project <P> --env dev --expect-status 200 --expect-json '$.x=y'
python probe_db.py query --engine postgres --sql "select ..." --project <P> --env dev --expect-rows 1
python probe_kafka.py consume --topic <t> --project <P> --env dev --expect-contains '...'
python probe_redis.py get <key> --project <P> --env dev --expect-exists
# Ghi cổng integration (advisory) nếu có chạy:
python run_log.py record-gate <RID> integration --verdict pass --summary "<scenario> ok"
```
> Guard: thao tác **ghi** vào `prod` bị từ chối trừ khi `--allow-prod`. Pipeline mặc định test
> non-prod; KHÔNG tự ý chạy `--allow-prod` — nếu cần đụng prod phải hỏi xác nhận người dùng.

**Cổng CI Jenkins** (tuỳ chọn): `python jenkins.py build --project <P> --env dev --wait` → chỉ qua khi result=SUCCESS.

> Probe trả `{"error":true}` = **không kiểm được** (service down/thiếu config), KHÔNG phải đạt —
> coi như chưa verify, không được giao MR dựa trên đó.

## 5. Deliver

**Pre-MR review (gate `review`, chạy TRƯỚC khi tạo MR, KHÔNG post lên MR):** review diff thật
bằng agent headless, trả verdict JSON:
```bash
cd ../../auto-dev/tools
python review_gate.py run --root "<clone_dir>" --base "$TB" --branch "bugfix/<task_id>-<short>" > r.json
cd ../../dev-automation/tools
python run_log.py record-gate <RID> review --json r.json    # passed=false nếu có blocker
```
- `blockers` → sửa hết rồi review lại (đừng tạo MR khi còn blocker). `warnings` → cân nhắc.

**Đối chiếu acceptance criteria (gate `ac`, suy từ sổ AC):**
```bash
python run_log.py ac-map <RID> AC1 --evidence "test ReportServiceTest / probe_api 200"
# ... map từng AC tới bằng chứng; AC ngoài phạm vi: ac-waive <RID> ACx --note "..."
```
> Ở `mode=auto`: nếu `r.json.passed=false` (còn blocker) hoặc còn AC `open` → **DỪNG, KHÔNG tạo
> MR** (cổng `deliver` sẽ chặn ở `advance` cuối). Ở `mode=checkpoint`: trình các phát hiện này cho
> người duyệt ở mốc `before_mr` dưới đây.

### ✋ Checkpoint `before_mr`
Trình bày: kết quả test xanh + verdict review + AC đã map + tóm tắt diff. Chờ duyệt:
```bash
python run_log.py checkpoint <RID> before_mr approved
GITLAB_PROJECT_ID=<id> python gitlab_api.py create-mr "bugfix/<task_id>-<short>" "Fix: <tiêu đề>" "$TB"
python run_log.py field <RID> mr_url "<url trả về>"
```

### ✋ Checkpoint `before_notify`
Người thật sẽ thấy notification → confirm trước:
```bash
python run_log.py checkpoint <RID> before_notify approved
python notifier.py mr-created <task_id> "<mr_url>"
python azure_devops.py state <task_id> Resolved
python run_log.py advance <RID> deliver    # done iff gate review+ac đạt (auto chặn / checkpoint báo)
```

## Resume

```bash
python run_log.py get <RID>          # xem dừng ở đâu
python run_log.py list --open        # các run chưa xong
```
Vào lại ở stage đầu tiên chưa `done`. Nếu một checkpoint còn `pending`, trình bày lại artifact của stage đó và xin duyệt trước khi đi tiếp.

## Bảng xử lý lỗi

| Tình huống | Hành động |
|---|---|
| Không tìm được project trong registry | Hỏi `clone_dir` + lệnh test, dùng `--cwd/--cmd` |
| Test fail do test cũ lỗi thời | Cập nhật test, ghi `note`, không tính là fix sai |
| Test timeout (>30 phút) | `test_runner` trả `timed_out`; chia nhỏ hoặc tăng `--timeout` |
| Fix cần đổi schema | Kèm migration script, ghi rõ trong mô tả MR |
| Hết 3 lần retry vẫn đỏ | `stage test failed`, dừng, bàn giao người |
