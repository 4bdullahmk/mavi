"""Portable API wrapper for the shared declarative XLSX implementation."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

_SOURCE = Path(__file__).resolve().parents[1] / "app" / "SpreadsheetTools.py"
_SPEC = spec_from_file_location("mavi_spreadsheet_tools", _SOURCE)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("Mavi spreadsheet tools are unavailable.")
_MODULE = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)

MAX_SPEC_BYTES = _MODULE.MAX_SPEC_BYTES
MAX_SHEETS = _MODULE.MAX_SHEETS
MAX_ROWS = _MODULE.MAX_ROWS
MAX_COLUMNS = _MODULE.MAX_COLUMNS
SPEC_GUIDE = _MODULE.SPEC_GUIDE
schema_help = _MODULE.schema_help
validate_spec = _MODULE.validate_spec
build_workbook = _MODULE.build_workbook
create_workbook = _MODULE.create_workbook
