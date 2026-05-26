#!/usr/bin/env python3
"""Project, sprint, workspace, and list/board tools for eTask AI-agent.

Wraps ProjectTools, WorkspaceTools, and ListTaskTools via POST /api/ai/execute.

Usage:
  python3 projects.py get-project <project_id>
  python3 projects.py my-projects [--filter NAME] [--page N] [--size N]
  python3 projects.py get-sprint <sprint_id>
  python3 projects.py sprints <project_id>
  python3 projects.py project-for-list <list_id>

  python3 projects.py workspace <workspace_id> [--type my]
  python3 projects.py get-list <list_id>
  python3 projects.py lists <workspace_id>
  python3 projects.py my-lists
"""

import argparse
import sys

import client
import config


# ── Project wrappers ───────────────────────────────────────────────────────────

def get_project(project_id: str) -> dict:
    r = client.execute_tool("get_project", {"project_id": project_id})
    client.check_error(r, "get_project")
    return r


def query_my_projects(name_filter: str = None, page: int = 0, size: int = 20) -> dict:
    args = {"page": page, "size": size}
    if name_filter:
        args["name_filter"] = name_filter
    r = client.execute_tool("query_my_projects", args)
    client.check_error(r, "query_my_projects")
    return r


def get_sprint(sprint_id: str) -> dict:
    r = client.execute_tool("get_sprint", {"sprint_id": sprint_id})
    client.check_error(r, "get_sprint")
    return r


def query_sprints(project_id: str) -> dict:
    r = client.execute_tool("query_sprints", {"project_id": project_id})
    client.check_error(r, "query_sprints")
    return r


def get_project_for_list(list_id: str) -> dict:
    r = client.execute_tool("get_project_for_list", {"list_id": list_id})
    client.check_error(r, "get_project_for_list")
    return r


# ── Workspace wrappers ─────────────────────────────────────────────────────────

def get_workspace(workspace_id: str, ws_type: str = "my") -> dict:
    r = client.execute_tool("get_workspace", {"workspace_id": workspace_id, "type": ws_type})
    client.check_error(r, "get_workspace")
    return r


# ── List/Board wrappers ────────────────────────────────────────────────────────

def get_list(list_id: str) -> dict:
    r = client.execute_tool("get_list", {"list_id": list_id})
    client.check_error(r, "get_list")
    return r


def query_lists_by_workspace(workspace_id: str) -> dict:
    r = client.execute_tool("query_lists_by_workspace", {"workspace_id": workspace_id})
    client.check_error(r, "query_lists_by_workspace")
    return r


def query_my_lists() -> dict:
    r = client.execute_tool("query_my_lists", {})
    client.check_error(r, "query_my_lists")
    return r


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    missing = config.validate()
    if missing:
        print(f"[ERROR] Missing config: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(0)

    cmd = sys.argv[1]

    if cmd == "get-project":
        parser = argparse.ArgumentParser(prog="projects.py get-project")
        parser.add_argument("project_id")
        args = parser.parse_args(sys.argv[2:])
        client.print_json(get_project(args.project_id))

    elif cmd == "my-projects":
        parser = argparse.ArgumentParser(prog="projects.py my-projects")
        parser.add_argument("--filter", dest="name_filter", default=None)
        parser.add_argument("--page", type=int, default=0)
        parser.add_argument("--size", type=int, default=20)
        args = parser.parse_args(sys.argv[2:])
        client.print_json(query_my_projects(args.name_filter, args.page, args.size))

    elif cmd == "get-sprint":
        parser = argparse.ArgumentParser(prog="projects.py get-sprint")
        parser.add_argument("sprint_id")
        args = parser.parse_args(sys.argv[2:])
        client.print_json(get_sprint(args.sprint_id))

    elif cmd == "sprints":
        parser = argparse.ArgumentParser(prog="projects.py sprints")
        parser.add_argument("project_id")
        args = parser.parse_args(sys.argv[2:])
        client.print_json(query_sprints(args.project_id))

    elif cmd == "project-for-list":
        parser = argparse.ArgumentParser(prog="projects.py project-for-list")
        parser.add_argument("list_id")
        args = parser.parse_args(sys.argv[2:])
        client.print_json(get_project_for_list(args.list_id))

    elif cmd == "workspace":
        parser = argparse.ArgumentParser(prog="projects.py workspace")
        parser.add_argument("workspace_id")
        parser.add_argument("--type", dest="ws_type", default="my")
        args = parser.parse_args(sys.argv[2:])
        client.print_json(get_workspace(args.workspace_id, args.ws_type))

    elif cmd == "get-list":
        parser = argparse.ArgumentParser(prog="projects.py get-list")
        parser.add_argument("list_id")
        args = parser.parse_args(sys.argv[2:])
        client.print_json(get_list(args.list_id))

    elif cmd == "lists":
        parser = argparse.ArgumentParser(prog="projects.py lists")
        parser.add_argument("workspace_id")
        args = parser.parse_args(sys.argv[2:])
        client.print_json(query_lists_by_workspace(args.workspace_id))

    elif cmd == "my-lists":
        client.print_json(query_my_lists())

    else:
        print(f"Unknown command: {cmd}\n", file=sys.stderr)
        print(__doc__)
        sys.exit(1)
