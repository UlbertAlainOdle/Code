"""Launcher for the api-hardening experiment: boots the real server module.

Runs inside the sandbox PROJECT_ROOT so every knowledge-base write (usage
ledger, sessions) lands in the experiment directory instead of the repo.
"""

import importlib.util
import os
import sys

ROOT = os.environ["EXP_REPO_ROOT"]
sys.path.insert(0, ROOT)
spec = importlib.util.spec_from_file_location(
    "saya_server_experiment", os.path.join(ROOT, "server5-22B----.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

import uvicorn  # noqa: E402

uvicorn.run(
    module.app,
    host="127.0.0.1",
    port=int(os.environ.get("EXP_PORT", "8100")),
    log_level="warning",
)
