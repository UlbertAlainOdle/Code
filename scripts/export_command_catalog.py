"""Export the unified CommandCatalog to minecraft_worker/command_catalog.json.

The Node-side contract test reads this file to prove the worker really
parses every verb the catalog promises.  Run after changing
saya_app/services/minecraft_catalog.py:

    python -B scripts/export_command_catalog.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saya_app.services.minecraft_catalog import catalog_view  # noqa: E402

TARGET = ROOT / "minecraft_worker" / "command_catalog.json"


def main():
    payload = catalog_view()
    payload["worker_verbs"] = sorted(
        {entry["worker_verb"] for entry in payload["commands"]}
    )
    TARGET.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {TARGET} ({len(payload['commands'])} commands)")


if __name__ == "__main__":
    main()
