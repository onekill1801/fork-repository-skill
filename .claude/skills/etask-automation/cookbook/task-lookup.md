# Cookbook: Task Lookup & Navigation

## Purpose
Find, browse, and inspect tasks in eTask — read-only operations, no risk of data loss.

## Prerequisites
- `.env` has `ETASK_BASE_URL` and `ETASK_PAT_TOKEN`
- Run `python3 config.py` to verify; run from `tools/` directory

## Steps

### 1. Find my assigned tasks (quickest start)
```bash
cd .claude/skills/etask-automation/tools
python3 search.py my-tasks
```
Optionally filter:
```bash
python3 search.py my-tasks --status OPEN,IN_PROGRESS --size 50
python3 search.py my-tasks --query "payment" --start 2026-01-01T00:00:00Z
```

### 2. Broad task search (all accessible tasks)
```bash
python3 search.py tasks --query "login bug" --priority HIGH,URGENT
python3 search.py tasks --list <list_task_id> --status IN_PROGRESS
python3 search.py tasks --project <project_id> --created-by johndoe --page 0 --size 20
```

### 3. Get full task details
```bash
python3 tasks.py get <task_id>
```

### 4. List subtasks (children of a task)
```bash
python3 tasks.py subtasks <parent_task_id>
```

### 5. Query all tasks in a specific list/board
```bash
python3 tasks.py query <list_task_id>
python3 tasks.py query <list_task_id> --status OPEN
python3 tasks.py query <list_task_id> --parent <parent_id>
```

### 6. Get tasks in a sprint
```bash
python3 tasks.py by-sprint <sprint_id>
```
If you don't know the sprint ID:
```bash
python3 projects.py my-projects           # find project
python3 projects.py sprints <project_id>  # list sprints
python3 tasks.py by-sprint <sprint_id>
```

### 7. Search in workspace dashboard
```bash
python3 search.py dashboard <workspace_id>
python3 search.py dashboard <workspace_id> --status OPEN,IN_PROGRESS --query "urgent"
```

### 8. Find linkable tasks (for cross-task referencing)
```bash
python3 search.py candidates --query "invoice" --exclude <id1>,<id2>
```

## Error Recovery

| Error | Likely cause | Fix |
|---|---|---|
| `401 Unauthorized` | Expired or missing PAT | Re-generate token; update `ETASK_PAT_TOKEN` in `.env` |
| `Connection refused` | Wrong `ETASK_BASE_URL` | Check URL in `.env`; confirm server is running |
| `SSL: CERTIFICATE_VERIFY_FAILED` | Self-signed cert | Set `ETASK_VERIFY_SSL=false` in `.env` |
| `error: Elasticsearch` | ES cluster is down | Fall back to `tasks.py query <list_id>` (uses DB directly) |
| Empty results | User has no assignments | Try `search.py tasks` with explicit filters |
