"""Owned single-process entry point; acquire the lease before model imports."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saya_app.services.qq_lifecycle import InstanceLease, InstanceInUse, process_identity
from saya_app.services.state_storage import atomic_save_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("invalid port")
    directory = ROOT / ".runtime" / "qq-service"
    try:
        with InstanceLease(directory / "backend.lock"):
            import os
            os.chdir(ROOT)
            atomic_save_json(directory / "backend-owner.json", process_identity(os.getpid()))
            import uvicorn
            uvicorn.run("server5-22B----:app", host="127.0.0.1", port=args.port, workers=1)
    except InstanceInUse as error:
        print(str(error))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
