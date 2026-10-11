"""Tiny helper to upsert KEY=value lines in the project's .env file."""

from pathlib import Path

from django.conf import settings


def upsert_env(values, path=None):
    path = Path(path or settings.BASE_DIR / ".env")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in remaining and not line.lstrip().startswith("#"):
            lines[i] = f"{key}={remaining.pop(key)}"
    lines.extend(f"{k}={v}" for k, v in remaining.items())
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
