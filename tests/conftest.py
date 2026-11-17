"""Shared pytest configuration.

Environment isolation so tests can never pollute the REAL stores:
- SAYA_GAME_MEMORY=0   planner-executor tests must not write game_task
                       memories into the live knowledge base;
- SAYA_SLU_CORPUS=0    routing tests must not append smoke phrases to the
                       live SLU corpus (was set per-file before, now global).
"""

import os

os.environ.setdefault("SAYA_GAME_MEMORY", "0")
os.environ.setdefault("SAYA_SLU_CORPUS", "0")
