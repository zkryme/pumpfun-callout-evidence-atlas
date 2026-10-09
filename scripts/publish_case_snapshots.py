"""Publish isolated investigation snapshots for the static case selector."""
from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PROFILES = (
    "EqBQhoU88ty3a3aDphDRCBuiRirjUsFSFi4UHGY64qvv",
    "215nhcAHjQQGgwpQSJQ7zR26etbjjtVdW74NLzwEgQjP",
)


def main() -> None:
    destination = ROOT / "web" / "public" / "data" / "cases"
    destination.mkdir(parents=True, exist_ok=True)
    for profile in PROFILES:
        source = ROOT / "investigations" / profile / "output" / "analysis.json"
        if source.exists():
            shutil.copy2(source, destination / f"{profile}.json")


if __name__ == "__main__":
    main()
