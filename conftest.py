"""
conftest.py — repo-root pytest configuration

Inserts the repo root into sys.path so that `from app.x import y`
works regardless of which directory pytest is invoked from.
This is a fallback for pytest versions that don't support `pythonpath`
in pytest.ini (requires pytest >= 7.0 for that feature).
"""

import sys
from pathlib import Path

# Repo root = the directory that contains this conftest.py
REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
