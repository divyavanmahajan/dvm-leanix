"""
Token persistence for lean-ix.

Tokens are stored in ~/.lean-ix/tokens.json as a JSON object mapping
workspace URL → token string. The file is created with restricted
permissions so other users cannot read it.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Optional


def _token_file() -> Path:
    path = Path.home() / ".lean-ix" / "tokens.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_all() -> dict[str, str]:
    f = _token_file()
    if not f.exists():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_all(data: dict[str, str]) -> None:
    f = _token_file()
    text = json.dumps(data, indent=2)
    f.write_text(text, encoding="utf-8")
    # Restrict to owner read/write on POSIX
    try:
        f.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except (AttributeError, NotImplementedError):
        pass  # Windows — best-effort


def save_token(url: str, token: str) -> None:
    """Persist a Bearer token for the given workspace URL."""
    data = _load_all()
    data[url.rstrip("/")] = token
    _save_all(data)


def load_token(url: str) -> Optional[str]:
    """Return the saved Bearer token for *url*, or None if not found."""
    return _load_all().get(url.rstrip("/"))


def clear_token(url: str) -> None:
    """Remove the saved token for *url* (e.g. after a confirmed 401)."""
    data = _load_all()
    data.pop(url.rstrip("/"), None)
    _save_all(data)
