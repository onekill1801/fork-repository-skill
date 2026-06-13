---
name: FPT Chat Automation
description: >
  Read-only automation over FPT Chat (internal corporate messenger) via its REST
  API at api-chat.fpt.com: list conversations, read message history & shared
  media, search the user directory, browse todos, check session. Auth is a
  session Bearer token + x-app/x-lang/x-request-id headers.
  Trigger phrases: "fpt chat", "list my conversations", "đọc tin nhắn fpt chat",
  "fchat conversations", "fpt chat todos", "search fpt chat user", "my fpt chat groups".
---

# FPT Chat Automation

## Purpose

Query FPT Chat from the agent: conversations, participants, message history,
shared files/links/media, todos, and the user directory. **Read-only** — this
skill does not send messages or mutate data.

> Reverse-engineered from an authenticated traffic capture (chrome://net-export)
> + public JS fingerprinting. Endpoint paths/methods/params are verified;
> response **body shapes are not guaranteed** — treat tool output as raw server JSON.

## Scope & hard limits (read first)

- **Read + two verified writes:** `todos.py create` (REST) and `send.py text`
  (send a chat message over the SocketCluster realtime socket). Both are gated
  behind `--yes` and dry-run by default. Other create/update/delete is unwired.
- **Confirm before writing:** only pass `--yes` after the user explicitly
  approves (repo `[WRITE]` guardrail). `send.py` posts a real message people see.
- **E2E limit:** `send.py` sends `content` as PLAINTEXT — correct only for
  NON-secure conversations. Secure groups encrypt content (beatchat); that crypto
  is NOT implemented, so do not send into secure/encrypted groups.
- **Message content is END-TO-END ENCRYPTED** (beatchat keypair). `messages.py list`
  returns ciphertext bodies — metadata (sender, time, ids, media refs) is usable,
  decrypting text is out of scope.
- **Sending messages is NOT here** — it rides a SocketCluster realtime socket
  (`wss://realtime-chat.fpt.com`), which this REST skill does not touch.
- **Authorization:** FPT Chat is an internal E2E messenger. Use this on your own
  account; clear any broader/automated use with the FPT Chat team.

## Configuration (.env at repo root)

| Key | Purpose |
|---|---|
| `FCHAT_BASE_API_URL` | REST base (default `https://api-chat.fpt.com`) |
| `FCHAT_BEARER_TOKEN` | Session JWT (secret). Copy from a logged-in `chat.fpt.com` tab → DevTools → any `api-chat` request → Request Headers → `authorization: Bearer …` |
| `FCHAT_X_APP` | Value of the `x-app` request header (copy from the same request) |
| `FCHAT_LANG` | `vi` or `en` (default `vi`) |
| `FCHAT_VERIFY_SSL` | `true` default; `false` only for internal certs (warns) |

The token is short-lived: on `401` the tools tell you to refresh it.

## Available tools (`.claude/skills/fpt-chat-automation/tools/`)

`cd` into `tools/` first. Windows: `python` · macOS/Linux: `python3`.

```
python config.py                                  # validate .env
python auth.py whoami                             # verify token (GET /user/me)

python users.py me | setting | server-key
python users.py search [--q TEXT] [--group ID] [--limit N] [--page N]   # directory (GET)
python users.py lookup --ids ID1,ID2                                    # batch resolve (POST)

python groups.py list [--limit N] [--before ISO] [--filter DIRECT_CHAT] # ISO = latestMessageAt cursor
python groups.py search [--q TEXT] [--limit N] [--page N]               # global conversation search
python groups.py get <group_id>
python groups.py participants <group_id> [--limit N] [--page N]
python groups.py setting <group_id>
python groups.py folders

python messages.py list <group_id> [--limit N]    # bodies are E2E ciphertext
python messages.py scheduled <group_id>
python messages.py media <group_id> --type MEDIA|FILE|LINK|VOICE [--limit N]
python messages.py marked [--status UNDONE] [--limit N]
python messages.py count-marked [--status UNDONE]

python todos.py list [--type BY_ME|TO_ME|IMPORTANT] [--group ID] [--filter EXPIRED]
python todos.py count-expired
python todos.py create --group ID --title T [--detail D] [--due ISO] [--important] [--yes]  # [WRITE] dry-run without --yes
python todos.py delete --id TODO_ID [--yes]                                  # [WRITE] delete a todo; dry-run without --yes

python send.py text   --group ID --content "..." [--group-type TYPE] [--yes] # [WRITE] send message over WS; dry-run without --yes
python send.py recall --group ID --inc MESSAGE_ID_INC [--yes]                # [WRITE] recall (delete for everyone); dry-run without --yes

python listen.py [--reply claude|notify|off] [--cooldown 30]                 # long-running: listen for incoming messages
python auth.py refresh | token-status                                        # token mgmt (auto-refresh is automatic)
```

Realtime send uses `ws_client.py` (a minimal stdlib WebSocket client) against
`FCHAT_WS_URL` (default `wss://realtime-chat.fpt.com/realtime`), authenticating
with the JWT as `Sec-WebSocket-Protocol`.

```

python common.py settings | notification-setting | stickers
```

All tools print JSON; errors print `{"error": true, "status": ..., "message": ...}`
to stdout and exit non-zero — read and handle, don't treat as a crash.

## Listener (auto-reply to DMs)

`listen.py` keeps a realtime WS connection and watches incoming messages:
- **Direct (1-1) messages from others** → writes a context file to
  `temp/fchat_incoming/` and **forks a `claude` terminal** (Pro subscription, no
  API key — via the `fork-terminal` tool) to draft & send a reply through `send.py`.
- **Group messages** → logged only, never auto-replied.
- Own messages / typing / seen → ignored (the `senderId == me` filter prevents reply loops).
- E2E-encrypted DM content is detected and flagged "unreadable" (no plaintext reply).

Modes: `--reply claude` (default, spawn Claude), `--reply notify` (just print),
`--reply off` (log only). `--cooldown N` throttles spawns per conversation.
Heartbeat + auto-reconnect + token refresh are built in. Stop with Ctrl+C.

## Workflows

1. **Browse conversations & history** → `cookbook/read-conversations.md`
   `groups.py list` → pick id → `groups.py get` / `participants` → `messages.py list` / `media`.
2. **Todos overview** → `cookbook/todos-overview.md`
   `todos.py list --type TO_ME` and `--filter EXPIRED`, `todos.py count-expired`.
3. **Headless QR login (best-effort)** → `cookbook/headless-login.md`
   Only if you can't copy a token; bodies unverified.

## Routing

| User intent | Tool |
|---|---|
| "list my chats / conversations", "nhóm của tôi" | `groups.py list` |
| "search conversations / find a group", "tìm nhóm" | `groups.py search --q` |
| "who's in <group>" | `groups.py participants` |
| "create a todo / task in <group>", "tạo việc" | `todos.py create` (confirm → `--yes`) |
| "delete task / xoá task" | `todos.py delete --id` (confirm → `--yes`) |
| "send a message to <group>", "gửi tin nhắn" | `send.py text` (confirm → `--yes`; non-secure only) |
| "recall / thu hồi tin nhắn" | `send.py recall --group --inc` (confirm → `--yes`) |
| "read messages in <group>", "đọc tin nhắn" | `messages.py list` |
| "files/links/media in <group>" | `messages.py media --type ...` |
| "find user X", "tìm người dùng" | `users.py search --q X` |
| "my todos", "việc cần làm", "expired tasks" | `todos.py list` / `count-expired` |
| "am I logged in / who am I" | `auth.py whoami` |

## Slash command

- `/fpt-chat` → list conversations and summarize unread/todos (see `.claude/commands/fpt-chat.md`).

## Important rules

1. Confirm before any `--yes` write (`todos.py create`, `send.py text`). Dry-run first, show the user the frame/body, send only on explicit approval.
2. `send.py` is plaintext-only → never target a secure/encrypted conversation with it.
3. Respect rate limits — on `429`, back off (Retry-After is surfaced in the error).
4. Never hardcode the token; always via `config.py` / `.env`.
5. Don't log full message payloads to shared places — they may be sensitive even when encrypted.
