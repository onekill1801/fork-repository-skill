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

## Chuẩn hoá sau intake

Bất kể nguồn nào, sau intake phải có đủ:

| Field | Lấy từ |
|---|---|
| `run_id` | `<project>-<task_id>` hoặc `adhoc-<slug>` |
| `task_id` | id nguồn (rỗng nếu adhoc) |
| `project` | khớp `./work/projects.json` (hỏi nếu chưa có registry) |
| `type` | bugfix \| feature |
| `title` | tiêu đề task |
| `clone_dir` + `test_cmd` | registry, hoặc hỏi người dùng |

Thiếu `project`/`clone_dir` mà registry chưa tồn tại → **hỏi người dùng** đường dẫn code và
lệnh test trước khi tiếp tục (đừng đoán).

## GĐ2 — poll tự động (chưa bật)

Khi muốn pipeline tự nhận task mới (không cần gõ lệnh), dùng `/loop` hoặc `/schedule` để
chạy định kỳ: `azure_devops.py list` + `search.py my-tasks` → so với run-log đã có
(`run_log.py list`) → task mới nào chưa có run thì khởi tạo. **Vẫn giữ checkpoint** —
poll chỉ tự *nhận*, không tự *giao*. Đây là việc của GĐ2, không bật ở GĐ1.
