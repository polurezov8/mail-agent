from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Account:
    name: str
    credentials_path: Path
    token_path: Path

    @property
    def is_authorized(self) -> bool:
        return self.token_path.exists()


def load_accounts(creds_dir: str | Path = "creds") -> list[Account]:
    raw = os.environ.get("GMAIL_ACCOUNTS", "personal")
    names = [n.strip() for n in raw.split(",") if n.strip()]
    base = Path(creds_dir)
    accounts: list[Account] = []
    for name in names:
        accounts.append(
            Account(
                name=name,
                credentials_path=base / f"{name}_credentials.json",
                token_path=base / f"{name}_token.json",
            )
        )
    return accounts
