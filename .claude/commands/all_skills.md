# List Available Skills & Commands

## Project skills (`.claude/skills/`)

| Skill | Trigger / purpose |
|-------|-------------------|
| **fork-terminal** | "fork terminal", "new terminal" — spawn parallel agents or CLI in a new window |
| **dev-automation** | "review MR", "fix bug", "list my tasks", Azure DevOps + GitLab workflows |
| **skill-scaffold** | "extract tools", "scaffold skill" — import app tools into new skills |
| **etask-automation** | "create task", "search tasks", "my tasks", "show statistics", "list projects" — FIS eTask platform |
| **fpt-chat-automation** | "fpt chat", "list my conversations", "fpt chat todos", "đọc tin nhắn fpt chat" — read-only FPT Chat REST (conversations, messages, todos, directory) |

## Slash commands (`.claude/commands/`)

| Command | Purpose |
|---------|---------|
| `/prime` | Onboard: read all skills, cookbooks, and tools in this repo |
| `/all_skills` | This list |
| `/list-tasks` | List Azure DevOps work items assigned to you |
| `/read-task <id>` | Show one work item |
| `/review-mr <iid>` | Code review a GitLab merge request |
| `/fix-bug <id>` | Fix bug workflow (branch → code → MR → notify) |
| `/implement-feature <id>` | New feature workflow |
| `/notify-tester <id> [url]` | Post tester notification on Azure DevOps |
| `/extract-tools` | Phase 1: scan app → `temp/tool-inventory.yaml` |
| `/design-skill` | Phase 2: inventory → `temp/skill-design.md` |
| `/scaffold-skill` | Phase 3: generate new skill under `.claude/skills/` |
| `/etask-search [query]` | Search eTask tasks (full-text or my assigned tasks) |
| `/etask-create <name> [list_id]` | Create a new eTask task |
| `/etask-projects [filter]` | Browse eTask projects, sprints, and workspaces |
| `/etask-stats [scope]` | Show eTask analytics and statistics |
| `/fpt-chat` | FPT Chat: list conversations + todos overview (read-only) |

Skills run automatically from natural language. Slash commands run the same workflows explicitly.

**Import tools from another repo:** copy prompt from `.claude/skills/skill-scaffold/prompts/run_in_source_app_prompt.md` into your app project first.
