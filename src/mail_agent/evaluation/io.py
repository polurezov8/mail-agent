from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from .dataset import LabeledExample


class FixtureFile(BaseModel):
    examples: list[LabeledExample]


def load(path: Path) -> list[LabeledExample]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text())
    if not raw or not raw.get("examples"):
        return []
    return FixtureFile.model_validate(raw).examples


def load_all(personal_path: Path) -> list[LabeledExample]:
    """Merge personal fixtures (gitignored) with public synthetic fixtures
    (sibling file). De-duplicates by (account, email id)."""
    personal = load(personal_path)
    public = load(personal_path.with_name("fixtures.public.yaml"))
    seen: set[tuple[str, str]] = set()
    merged: list[LabeledExample] = []
    for ex in (*personal, *public):
        key = (ex.email.account, ex.email.id)
        if key in seen:
            continue
        seen.add(key)
        merged.append(ex)
    return merged


def save(path: Path, examples: list[LabeledExample]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    file = FixtureFile(examples=examples)
    path.write_text(
        yaml.safe_dump(file.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    )
