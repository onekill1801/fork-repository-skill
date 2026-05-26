#!/usr/bin/env python3
"""Configuration loader for etask-automation tools.

Reads settings from environment variables or a .env file in the project root.
Zero external dependencies — uses only Python stdlib.

Usage:
  python3 config.py          # validate and print current settings
"""

import os
import re

_ENV_LOADED = False


def _strip_inline_comment(value: str) -> str:
    """Remove trailing # comment from .env values (not inside quotes)."""
    value = value.strip()
    if not value or value[0] in "\"'":
        return value.strip("\"'")
    hash_at = value.find("#")
    if hash_at >= 0:
        value = value[:hash_at]
    return value.strip()


def _load_dotenv():
    """Parse .env file and inject into os.environ (only once)."""
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    _ENV_LOADED = True

    env_path = os.path.join(os.getcwd(), ".env")
    if not os.path.isfile(env_path):
        search = os.path.dirname(os.path.abspath(__file__))
        for _ in range(6):
            candidate = os.path.join(search, ".env")
            if os.path.isfile(candidate):
                env_path = candidate
                break
            search = os.path.dirname(search)
        else:
            return

    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r'^([A-Za-z_]\w*)=(.*)$', line)
            if match:
                key = match.group(1)
                value = _strip_inline_comment(match.group(2))
                os.environ.setdefault(key, value)


def get(key: str, default: str = "") -> str:
    """Get a config value from environment, loading .env if needed."""
    _load_dotenv()
    return os.environ.get(key, default).strip()


def base_url() -> str:
    """Return ETASK_BASE_URL with trailing slash stripped."""
    return get("ETASK_BASE_URL", "http://localhost:8080").strip().rstrip("/")


def pat_token() -> str:
    return get("ETASK_PAT_TOKEN").strip()


def pat_header_name() -> str:
    """HTTP header name for PAT authentication (default: X-eTask-PAT)."""
    return get("ETASK_PAT_HEADER", "X-eTask-PAT").strip()


def verify_ssl() -> bool:
    return get("ETASK_VERIFY_SSL", "true").lower() not in ("false", "0", "no")


def validate() -> list:
    """Return list of missing required config keys."""
    required = ["ETASK_BASE_URL", "ETASK_PAT_TOKEN"]
    return [k for k in required if not get(k)]


if __name__ == "__main__":
    missing = validate()
    if missing:
        print(f"[ERROR] Missing required config: {', '.join(missing)}")
        print("  Set these in your .env file:")
        print("    ETASK_BASE_URL=https://etask.example.com")
        print("    ETASK_PAT_TOKEN=<your-personal-access-token>")
    else:
        print("[OK] Config valid.")
        print(f"  ETASK_BASE_URL   = {base_url()}")
        print(f"  ETASK_PAT_TOKEN  = {'*' * 8}...{pat_token()[-4:] if len(pat_token()) > 4 else '****'}")
        print(f"  ETASK_VERIFY_SSL = {verify_ssl()}")
