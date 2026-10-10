"""Shared models (PLAN.md section 3), imported from Brain's backend/contracts.py.

Brain owns contracts.py. Until it is on main, fall back to the verbatim copy in
_contracts_fallback.py so Data's code and tests run. Once contracts.py exists we
always use Brain's classes, so there is only one set of models at runtime.

Works whether the app is started as `uvicorn backend.main:app` (package
`backend.data`) or from inside backend/ as `uvicorn main:app` (package `data`).
"""
import importlib

_root = __package__.rpartition(".")[0]          # "backend" or ""
_name = f"{_root}.contracts" if _root else "contracts"

try:
    _mod = importlib.import_module(_name)
    USING_FALLBACK = False
except ModuleNotFoundError as exc:
    if exc.name not in (_name, _root):
        raise
    from . import _contracts_fallback as _mod
    USING_FALLBACK = True

Expression = _mod.Expression
VitalsReading = _mod.VitalsReading
VitalsBatch = _mod.VitalsBatch
Baseline = _mod.Baseline
WindowStats = _mod.WindowStats
QuestionStats = _mod.QuestionStats

__all__ = ["Expression", "VitalsReading", "VitalsBatch", "Baseline", "WindowStats",
           "QuestionStats", "USING_FALLBACK"]
