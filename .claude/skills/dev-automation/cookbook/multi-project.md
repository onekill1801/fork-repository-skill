# Làm việc trên nhiều project (multi-project)

Bộ skill này (`fork-repository-skill`) là **toolset dùng chung** — đứng yên, không chứa code
project. Code của từng project sống trong **workspace** riêng: `~/work/`.

```
~/work/
  projects.json     # registry: project → {gitlab_project_id, azure_project, clone_dir, ...}
  proj.sh           # shell switcher cho terminal của con người
  .profiles/        # (tuỳ chọn) file profile per-project
  etask/            # git clone idaas/etask
  <project-B>/      # git clone ...
```

## Nguyên lý

Tool đọc config qua `config.get()` → ưu tiên **biến môi trường** rồi mới tới `.env`
(`os.environ.setdefault`). Vì vậy chỉ cần export đúng `GITLAB_PROJECT_ID` /
`AZURE_DEVOPS_PROJECT` là cùng một bộ tool trỏ sang project khác — **không sửa code, không sửa
`.env`**. Token Azure/GitLab giữ nguyên 1 chỗ trong skill `.env` (dùng chung cho cả org).

## Cho con người (terminal)

```bash
source ~/work/proj.sh     # thêm dòng này vào ~/.bashrc
proj                      # liệt kê project
proj etask                # export env + cd vào ~/work/etask
# từ đây mọi lệnh dev-automation tự trỏ đúng project etask
```

## Cho agent (Claude Code)

Đọc `~/work/projects.json` để lấy `gitlab_project_id` / `azure_project` / `clone_dir` của
project đang làm, rồi **prefix env inline** mỗi lệnh (vì mỗi Bash call là shell mới):

```bash
# ví dụ project có gitlab_project_id=5401, azure_project=KYTA-all-in-one
GITLAB_PROJECT_ID=5401 AZURE_DEVOPS_PROJECT=KYTA-all-in-one \
  python3 gitlab_api.py list-mrs opened
```

Bước viết code/commit/push chạy bên trong `clone_dir` của project tương ứng.

## Thêm project mới

1. Clone repo về workspace:
   ```bash
   git clone https://gitlab.fis.vn/<group>/<repo>.git ~/work/<name>
   ```
   (Để tránh nhúng token vào remote: clone xong set remote sạch + dùng credential helper.)
2. Lấy `GITLAB_PROJECT_ID`: mở repo trên GitLab → Settings → General (hoặc qua API).
3. Thêm 1 mục vào `~/work/projects.json`:
   ```json
   "<name>": {
     "gitlab_project_id": "<id>",
     "gitlab_path": "<group>/<repo>",
     "azure_project": "<azure project nếu khác>",
     "clone_dir": "/home/chungtv8/work/<name>",
     "default_target_branch": "develop"
   }
   ```
4. Xong — `proj <name>` hoặc prefix env là dùng được ngay.

## Lưu ý

- Token dùng chung: nếu một project nằm ở **org/GitLab khác**, phải thêm token riêng — khi đó
  khai báo cả `GITLAB_PRIVATE_TOKEN` / `AZURE_DEVOPS_PAT` trong profile của project đó.
- `clone_dir` là đường dẫn máy-cụ-thể → `projects.json` nằm ở `~/work` (không commit vào skill repo).
