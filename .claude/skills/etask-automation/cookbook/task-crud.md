# Cookbook: Task CRUD

## Purpose
Create, update, complete, move, and delete tasks. Write operations — confirm
destructive actions with the user before executing.

## Prerequisites
- `.env` has `ETASK_BASE_URL` and `ETASK_PAT_TOKEN`
- PAT must have `task:write` scope
- Run from `tools/` directory

## ⚠️ Guardrails
- **`delete`** is irreversible — always show the task name and confirm first
- **Status change to DONE/CANCELLED** — show current status, ask user to confirm
- **Bulk operations** — cap at 20 per agent turn; warn user if more needed

## Steps

### 1. Discover available lists (if list ID unknown)
```bash
cd .claude/skills/etask-automation/tools
python3 projects.py my-lists
# or, if workspace ID is known:
python3 projects.py lists <workspace_id>
```

### 2. Create a root task
```bash
python3 tasks.py create --name "Fix login timeout" --list <list_task_id>
python3 tasks.py create --name "API Integration" --list <list_id> \
    --priority HIGH --due 2026-06-30T17:00:00Z --desc "Integrate payment gateway"
```

### 3. Create a subtask
```bash
python3 tasks.py create --name "Write unit tests" --list <list_id> --parent <parent_task_id>
```

### 4. Update task fields
```bash
# Update name only
python3 tasks.py update <task_id> --name "New Title"

# Update status
python3 tasks.py update <task_id> --status IN_PROGRESS

# Update multiple fields
python3 tasks.py update <task_id> --priority URGENT --due 2026-06-01T09:00:00Z

# Update description
python3 tasks.py update <task_id> --desc "Updated acceptance criteria: ..."
```

### 5. Complete a task (quick DONE shortcut)
```bash
python3 tasks.py complete <task_id>
```

### 6. Move task to another list
First confirm target list exists:
```bash
python3 projects.py my-lists          # find target list ID
python3 tasks.py move <task_id> <target_list_id>
```

### 7. Assign to sprint
```bash
python3 projects.py sprints <project_id>     # list sprints
python3 tasks.py assign-sprint <task_id> <sprint_id>
```

### 8. Delete a task ⚠️
**Always confirm with user before running this.**
```bash
# First: show the task so user can confirm
python3 tasks.py get <task_id>

# Then, after user confirms:
python3 tasks.py delete <task_id>
```

## Error Recovery

| Error | Likely cause | Fix |
|---|---|---|
| `403 Forbidden` | PAT missing `task:write` scope | Re-create PAT with correct scopes |
| `NOT_FOUND` | Task ID doesn't exist | Verify ID with `tasks.py get` |
| `INVALID_INPUT` | Missing required param | Check `tasks.py create --help` |
| `400 Bad Request` | Invalid date format | Use ISO 8601: `2026-06-30T17:00:00Z` |
