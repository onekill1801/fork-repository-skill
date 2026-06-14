#!/usr/bin/env python3
"""Governed query tool for eTask AI-agent (added for backend Phase 6.2.1).

Wraps the `governed_search` tool via POST /api/ai/execute — a SAFE, read-only DSL
query over whitelisted data. The server compiles the query (rejecting any
entity/field/op not on its whitelist), injects the tenant server-side, and enforces
a row limit. No raw SQL is sent.

Usage:
  python3 governed_search.py search --entity task \
      --filter "status_type:eq:overdue" --filter "priority:in:HIGH,URGENT" --limit 50
  python3 governed_search.py search --entity task --filter "name:contains:report"

Filter format: "field:op:value"  (repeatable --filter).
  - op with multiple values (e.g. in/between): give value as a comma list -> sent as array.
  - The server enforces the allowed entity/field/op whitelist; an invalid one returns
    a clear error (use that to discover what's permitted).
"""

import argparse
import sys

import client
import config

# ops whose value is naturally a list (comma-separated on the CLI -> array payload)
_LIST_OPS = {"in", "nin", "not_in", "between", "any", "all"}


def _parse_filter(raw: str) -> dict:
    """'field:op:value' -> {field, op, value}. Value split to list for list-ops."""
    parts = raw.split(":", 2)
    if len(parts) != 3:
        raise ValueError(f"bad --filter '{raw}', expected 'field:op:value'")
    field, op, value = parts[0].strip(), parts[1].strip(), parts[2]
    if op.lower() in _LIST_OPS and "," in value:
        value = [v.strip() for v in value.split(",")]
    return {"field": field, "op": op, "value": value}


def governed_search(entity: str, filters: list, limit=None) -> dict:
    args = {"entity": entity, "filters": filters}
    if limit is not None:
        args["limit"] = limit
    r = client.execute_tool("governed_search", args)
    client.check_error(r, "governed_search")
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

    if cmd == "search":
        parser = argparse.ArgumentParser(prog="governed_search.py search")
        parser.add_argument("--entity", default="task", help="whitelisted entity (currently: task)")
        parser.add_argument("--filter", dest="filters", action="append", default=[],
                            help="'field:op:value' (repeatable)")
        parser.add_argument("--limit", type=int, default=None, help="max rows (server caps, e.g. 200)")
        args = parser.parse_args(sys.argv[2:])
        try:
            filters = [_parse_filter(f) for f in args.filters]
        except ValueError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)
        client.print_json(governed_search(args.entity, filters, args.limit))

    else:
        print(f"Unknown command: {cmd}\n", file=sys.stderr)
        print(__doc__)
        sys.exit(1)
