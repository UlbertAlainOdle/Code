"""Preview or apply the legacy -> Memory V4 migration.

The default command is strictly read-only.  ``--apply`` is the only mode that
creates a V4 database, and it first copies every available legacy source into
an isolated timestamped backup directory.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from saya_app.services.memory_v4 import MemoryV4Migration  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kb-dir", type=Path, default=ROOT / "knowledge_base")
    parser.add_argument("--v4-path", type=Path, default=None)
    parser.add_argument("--json-out", type=Path, default=None, help="write the report as JSON")
    parser.add_argument("--out", type=Path, default=None, help="write a human-readable report")
    parser.add_argument("--apply", action="store_true", help="create the V4 store after making a source backup")
    args = parser.parse_args(argv)

    migration = MemoryV4Migration(args.kb_dir, args.v4_path)
    report = migration.preview()
    if args.apply:
        result = migration.apply(report)
        output = result
    else:
        output = report
    rendered = json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(rendered + "\n", encoding="utf-8")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    try:
        print(rendered)
    except UnicodeEncodeError:
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(rendered.encode(encoding, errors="replace").decode(encoding, errors="replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
