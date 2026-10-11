"""Data test setup: makes `backend.data` importable from anywhere in the repo.

Collected by a bare `pytest` (pytest.ini testpaths). test_queries_db.py runs against the real Tiger Data
service whenever TIGER_DATABASE_URL is set (it deletes every row it writes) and skips otherwise.
"""
import sys
from pathlib import Path

# Make `backend.data` importable when pytest runs from anywhere in the repo.
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
