"""JSON helpers for backups and machine readable output.

用于备份与机器可读输出的 JSON 辅助函数。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from centsible.errors import DataError, ValidationError

__all__ = ["dumps", "load_json", "write_json"]


def dumps(payload: Any) -> str:
    """Pretty, UTF-8 preserving JSON text.

    生成美观且保留 UTF-8 的 JSON 文本。"""

    return json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def write_json(path: str | Path, payload: Any) -> Path:
    destination = Path(path)
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(dumps(payload), encoding="utf-8")
    except OSError as exc:
        raise ValidationError(
            f"cannot write {destination}: {exc}", field="path"
        ) from exc
    return destination


def load_json(path: str | Path) -> Any:
    source = Path(path)
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DataError(f"JSON file not found: {source}") from exc
    except ValueError as exc:
        raise DataError(f"invalid JSON in {source}: {exc}") from exc
