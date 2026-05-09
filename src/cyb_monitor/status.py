from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
from typing import Any


def write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        **payload,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pid": os.getpid(),
    }
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp_path.replace(path)


def read_status(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"state": "missing", "path": str(path)}
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {"state": "error", "path": str(path), "error": str(exc)}
