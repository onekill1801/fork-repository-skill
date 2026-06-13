# Auto-Dev Pipeline — chi tiết câu lệnh

Pipeline đầy đủ cho một task: **Intake → Plan → Implement → Test → Deliver**, chế độ
**checkpoint** (dừng xin duyệt 3 mốc). Mọi tool ở `.claude/skills/dev-automation/tools/`.

Quy ước trong tài liệu này:
- `<RID>` = run_id, gợi ý `<project>-<task_id>` (vd `etask-12345`).
- `<P>` = tên project trong `./work/projects.json`.
- Đặt env inline mỗi lệnh GitLab/Azure khi làm đa project (xem `dev-automation/cookbook/multi-project.md`).

---

## 0. Khởi tạo run-log

```bash
cd .claude/skills/dev-automation/tools
python run_log.py init <RID> --task <task_id> --project <P> --type bugfix --title "<tiêu đề>"
```

Nếu chưa có `./work/projects.json`: hỏi người dùng đường dẫn thư mục code (`clone_dir`) và
lệnh test, rồi truyền trực tiếp qua `--cwd` / `--cmd` ở bước Test.

## 1. Intake — đọc task

Azure: `python azure_devops.py get <task_id>` · eTask: xem `cookbook/intake.md`.
Trích: tiêu đề, mô tả, repro/acceptance, severity, work item liên quan.

## 2. Plan

```bash
python run_log.py stage <RID> plan active
```
- Định vị code liên quan (search class/method trong mô tả), đọc test hiện có, `git log` file ảnh hưởng.
- Viết plan cụ thể: **file sẽ sửa · hướng tiếp cận · chiến lược test (test nào chứng minh fix)**.
- Dùng `dev-automation/prompts/task_analysis_prompt.md` để cấu trúc phân tích.

```bash
python run_log.py stage <RID> plan done
```

### ✋ Checkpoint `after_plan`
Trình bày plan cho người dùng. Chờ duyệt. Khi được duyệt:
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
- Viết code trong `clone_dir` của project, theo `dev-automation/cookbook/java-standards.md`.
- **Thêm/chỉnh test** chứng minh thay đổi (bug: test fail trước fix, pass sau fix).
- Commit + push trong thư mục project.

```bash
python run_log.py stage <RID> implement done
```

## 4. Test (cổng bắt buộc)

```bash
python run_log.py stage <RID> test active
python test_runner.py run --project <P> --kind test
```
Đọc JSON trả về:
- `"passed": true` → `python run_log.py stage <RID> test done` → sang bước 5.
- `"passed": false` → đọc `log_tail`, sửa nguyên nhân, chạy lại. Lặp tối đa **3 lần**.
  Hết 3 lần vẫn đỏ:
  ```bash
  python run_log.py stage <RID> test failed
  python run_log.py note <RID> "test still red after 3 retries: <tóm tắt>"
  ```
  → DỪNG, báo người dùng, KHÔNG tạo MR.

Cổng phụ (tuỳ chọn, khuyến nghị cho feature lớn):
```bash
python test_runner.py run --project <P> --kind build
python test_runner.py run --project <P> --kind lint
```

Khi chưa có registry, thay `--project <P>` bằng `--cwd <dir> --cmd "<lệnh test>"`
(hoặc `--cwd <dir> --auto` để tự dò pom.xml/package.json/...).

**Cổng integration/e2e** (khi task chạm DB/API/Kafka/Redis) — chi tiết: `cookbook/stack-verify.md`.
Chạy ở env **non-prod** (dev/uat/sandbox) qua `--project <P> --env <env>`:
```bash
python flow_check.py --file ../../auto-dev/scenarios/<scenario>.json --project <P> --env dev
# hoặc probe lẻ cho phần thay đổi:
python probe_api.py call --url /api/... --project <P> --env dev --expect-status 200 --expect-json '$.x=y'
python probe_db.py query --engine postgres --sql "select ..." --project <P> --env dev --expect-rows 1
python probe_kafka.py consume --topic <t> --project <P> --env dev --expect-contains '...'
python probe_redis.py get <key> --project <P> --env dev --expect-exists
```
> Guard: thao tác **ghi** vào `prod` bị từ chối trừ khi `--allow-prod`. Pipeline mặc định test
> non-prod; KHÔNG tự ý chạy `--allow-prod` — nếu cần đụng prod phải hỏi xác nhận người dùng.

**Cổng CI Jenkins** (tuỳ chọn): `python jenkins.py build --project <P> --env dev --wait` → chỉ qua khi result=SUCCESS.

> Probe trả `{"error":true}` = **không kiểm được** (service down/thiếu config), KHÔNG phải đạt —
> coi như chưa verify, không được giao MR dựa trên đó.

## 5. Deliver

### ✋ Checkpoint `before_mr`
Trình bày: kết quả test xanh + tóm tắt diff. Chờ duyệt:
```bash
python run_log.py checkpoint <RID> before_mr approved
GITLAB_PROJECT_ID=<id> python gitlab_api.py create-mr "bugfix/<task_id>-<short>" "Fix: <tiêu đề>" "$TB"
python run_log.py field <RID> mr_url "<url trả về>"
```
- Self-review MR bằng `dev-automation` Workflow 1 (review-merge-request). Sửa hết issue tìm thấy.

### ✋ Checkpoint `before_notify`
Người thật sẽ thấy notification → confirm trước:
```bash
python run_log.py checkpoint <RID> before_notify approved
python notifier.py mr-created <task_id> "<mr_url>"
python azure_devops.py state <task_id> Resolved
python run_log.py stage <RID> deliver done
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
