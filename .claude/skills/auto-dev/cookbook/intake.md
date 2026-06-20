# Intake — nhận yêu cầu vào pipeline

Hai nguồn đã chọn: **Azure DevOps** và **eTask (FIS)**. Cộng nguồn **trực tiếp** (người dùng
mô tả yêu cầu / gõ `/auto-dev`). Đây là GĐ1 — intake **theo lệnh**, người dùng kích hoạt.
Poll tự động (cron) là GĐ2, ghi chú ở cuối.

## A. Trực tiếp

Người dùng mô tả yêu cầu hoặc gõ `/auto-dev <mô tả>`. Nếu không gắn với task hệ thống:
tự sinh `run_id` (vd `adhoc-<slug-ngắn>`), `--type` suy từ mô tả (bugfix/feature), không có `--task`.

## B. Azure DevOps

```bash
cd .claude/skills/dev-automation/tools
python azure_devops.py list            # task assigned @Me (chọn task để chạy)
python azure_devops.py get <task_id>   # đọc chi tiết một task
```
Map sang run-log:
- `--task` = work item id
- `--type` = `bugfix` nếu work item type = Bug, ngược lại `feature`
- `--project` = tên project trong registry khớp `azure_project` của work item

Đa project: prefix `AZURE_DEVOPS_PROJECT=<...>` mỗi lệnh.

## C. eTask (FIS)

```bash
cd .claude/skills/etask-automation/tools
python search.py my-tasks              # task của tôi
python tasks.py get <task_id>          # chi tiết
```
Map: `--task` = eTask task id, `--type` suy từ nhãn/loại task, `--project` khớp registry.
> eTask không gắn trực tiếp với GitLab repo → khi tạo branch/MR vẫn dùng `gitlab_api.py`
> với `GITLAB_PROJECT_ID` của project tương ứng trong `./work/projects.json`.

## Clarify — làm rõ yêu cầu mơ hồ (TRƯỚC triage/debate)

Hầu hết yêu cầu vào đều mơ hồ; debate giỏi mấy mà input mơ hồ vẫn lên plan sai. Chạy `clarify.py`
ngay sau khi đọc task, **trước** triage và debate:

```bash
cd .claude/skills/auto-dev/tools
python clarify.py analyze --type <bugfix|feature> --title "<tiêu đề>" --desc "<mô tả>"
#   [--backend claude]  escalate cho agent (câu hỏi sát task hơn); mặc định heuristic, không tốn token
```
Trả `{verdict, blocking_count, questions:[{category, ask, why, blocking, assumption}]}`:
- `category` ∈ scope · io · acceptance · edge_case · non_functional.
- `blocking=true` → **không trả lời thì gần như chắc code sai** (scope / I-O contract / định-nghĩa-done).
  `blocking=false` → chạy được với giả định mặc định.
- `verdict=needs_clarification` khi còn câu blocking; `pass` khi không.
- **Type-aware:** bugfix rõ ràng thường `pass` (AC ngầm = bug hết repro); feature mơ hồ dồn nhiều câu blocking.

**Xử lý (theo mode — đồng bộ gate `clarity` của stage plan):**
1. **Hỏi câu BLOCKING** cho người dùng (chỉ blocking; phần còn lại để giả định). Ở **auto mode**
   (tier trivial) thì KHÔNG hỏi — `needs_clarification` làm gate `clarity` **fail → chặn debate**
   (task quá mơ hồ không được tự chạy). Ở **checkpoint mode** thì hỏi người, gấp câu trả lời.
2. Gấp trả lời + giả định thành brief:
   ```bash
   python clarify.py brief --title "<...>" --desc-file task.txt \
     --answers-file answers.json --out ../../../temp/runs/<task_id>_brief.md
   #   answers.json: [{"ask":"...","answer":"..."}, {"ask":"...","assumption":"..."}]
   ```
   Brief (`temp/runs/<task_id>_brief.md`) thành `--desc` cho debate; `acceptance_seeds` → `ac-add`.
3. Ghi gate cho run-log (required trên stage plan):
   ```bash
   python ../../dev-automation/tools/run_log.py record-gate <RID> clarity --verdict pass --summary "0 blocking"
   # còn câu blocking chưa giải -> --verdict fail
   ```

## Triage — phân tier/mode (Hybrid autonomy)

Sau khi đọc task, phân loại để chọn mức tự động:

```bash
cd .claude/skills/auto-dev/tools
python triage.py classify --type <bugfix|feature> --title "<tiêu đề>" --desc "<mô tả>"
#   [--plan <path>]  đếm <target_files> nếu đã có spec
#   [--backend claude]  escalate cho agent khi mơ hồ (mặc định heuristic, không tốn token)
```
Trả `{tier, mode, reason, skip_debate}`:
- `trivial` → `mode=auto`, skip debate, gate tự chặn nếu đỏ.
- `standard|complex` → `mode=checkpoint`, giữ 3 mốc duyệt.
- Tín hiệu rủi ro (schema/migration/bảo mật/thanh toán/đồng thời...) → ép `complex`.

Override tay khi cần: `--force-tier` / `--force-mode`.

## Trích acceptance criteria

Bóc tiêu chí nghiệm thu từ mô tả/repro/AC của task vào sổ AC (đối chiếu ở Deliver):
```bash
python run_log.py ac-add <RID> --text "GET /api/report/export trả 200 + CSV đúng cột"
python run_log.py ac-add <RID> --text "input độc hại không gây SQLi"
# ... mỗi tiêu chí một dòng. Sổ trống = không ép (task adhoc/trivial).
```

## Chuẩn hoá sau intake

Bất kể nguồn nào, sau intake phải có đủ:

| Field | Lấy từ |
|---|---|
| `run_id` | `<project>-<task_id>` hoặc `adhoc-<slug>` |
| `task_id` | id nguồn (rỗng nếu adhoc) |
| `tier` / `mode` | `triage.py` (hoặc override tay) |
| `project` | khớp `./work/projects.json` (hỏi nếu chưa có registry) |
| `type` | bugfix \| feature |
| `title` | tiêu đề task |
| `acceptance_criteria` | `ac-add` từ mô tả/AC của task |
| `clone_dir` + `test_cmd` | registry, hoặc hỏi người dùng |

Thiếu `project`/`clone_dir` mà registry chưa tồn tại → **hỏi người dùng** đường dẫn code và
lệnh test trước khi tiếp tục (đừng đoán).

## GĐ2 — poll tự động (chưa bật)

Khi muốn pipeline tự nhận task mới (không cần gõ lệnh), dùng `/loop` hoặc `/schedule` để
chạy định kỳ: `azure_devops.py list` + `search.py my-tasks` → so với run-log đã có
(`run_log.py list`) → task mới nào chưa có run thì khởi tạo. **Vẫn giữ checkpoint** —
poll chỉ tự *nhận*, không tự *giao*. Đây là việc của GĐ2, không bật ở GĐ1.
