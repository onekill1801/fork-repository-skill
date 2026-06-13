#!/usr/bin/env python3
"""FPT Chat — message-query (read side) tools.

Endpoints verified from authenticated traffic capture.

IMPORTANT: message *content* is END-TO-END ENCRYPTED (beatchat keypair). These
tools return the raw server payload — text bodies will be ciphertext. Metadata
(sender, timestamps, ids, media references) is usable; decrypting content is a
separate, sensitive concern (needs the per-user private key + server key).

Usage:
  python messages.py list <group_id> [--limit N]
        # GET /message-query/group/{id}/message
  python messages.py scheduled <group_id>
        # GET /message-query/group/{id}/scheduled-message
  python messages.py media <group_id> --type MEDIA|FILE|LINK|VOICE [--limit N]
        # GET /message-query/message/search-media
  python messages.py marked [--status UNDONE] [--limit N]
        # GET /message-query/message/marked-message
  python messages.py count-marked [--status UNDONE]
        # GET /message-query/message/total-marked-message
"""

import argparse
import sys

import client
import config


def list_messages(group_id: str, limit=50) -> dict:
    r = client.api_get(f"/message-query/group/{group_id}/message", {"limit": limit})
    client.check_error(r, "list_messages")
    return r


def list_scheduled(group_id: str) -> dict:
    r = client.api_get(f"/message-query/group/{group_id}/scheduled-message")
    client.check_error(r, "list_scheduled")
    return r


def search_media(group_id: str, media_type: str, limit=30) -> dict:
    r = client.api_get("/message-query/message/search-media",
                       {"groupId": group_id, "type": media_type, "limit": limit})
    client.check_error(r, "search_media")
    return r


def list_marked(status="UNDONE", limit=30) -> dict:
    r = client.api_get("/message-query/message/marked-message",
                       {"status": status, "limit": limit})
    client.check_error(r, "list_marked")
    return r


def count_marked(status="UNDONE") -> dict:
    r = client.api_get("/message-query/message/total-marked-message", {"status": status})
    client.check_error(r, "count_marked")
    return r


if __name__ == "__main__":
    missing = config.validate()
    if missing:
        print(f"[ERROR] Missing config: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]
    if cmd == "list":
        p = argparse.ArgumentParser(prog="messages.py list")
        p.add_argument("group_id")
        p.add_argument("--limit", type=int, default=50)
        a = p.parse_args(sys.argv[2:])
        client.print_json(list_messages(a.group_id, a.limit))
    elif cmd == "scheduled":
        if len(sys.argv) < 3:
            print("usage: messages.py scheduled <group_id>", file=sys.stderr); sys.exit(1)
        client.print_json(list_scheduled(sys.argv[2]))
    elif cmd == "media":
        p = argparse.ArgumentParser(prog="messages.py media")
        p.add_argument("group_id")
        p.add_argument("--type", required=True, choices=["MEDIA", "FILE", "LINK", "VOICE"])
        p.add_argument("--limit", type=int, default=30)
        a = p.parse_args(sys.argv[2:])
        client.print_json(search_media(a.group_id, a.type, a.limit))
    elif cmd == "marked":
        p = argparse.ArgumentParser(prog="messages.py marked")
        p.add_argument("--status", default="UNDONE")
        p.add_argument("--limit", type=int, default=30)
        a = p.parse_args(sys.argv[2:])
        client.print_json(list_marked(a.status, a.limit))
    elif cmd == "count-marked":
        p = argparse.ArgumentParser(prog="messages.py count-marked")
        p.add_argument("--status", default="UNDONE")
        a = p.parse_args(sys.argv[2:])
        client.print_json(count_marked(a.status))
    else:
        print(f"Unknown command: {cmd}\n", file=sys.stderr)
        print(__doc__)
        sys.exit(1)
