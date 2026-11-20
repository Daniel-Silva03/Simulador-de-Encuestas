"""Escritura de libros Excel reproducibles byte a byte.

openpyxl guarda la fecha y hora actuales en las propiedades del documento y
en las entradas del ZIP. Aquí se fijan ambas para que la misma entrada dé los
mismos bytes (v1.1, §7.7). No se copian autor ni propiedades personales.
"""

from __future__ import annotations

import datetime as _dt
import io
import re
import zipfile
from typing import Any, Iterable, Sequence

FIXED = _dt.datetime(2000, 1, 1, 0, 0, 0)
# openpyxl vuelve a poner la hora actual en ``dcterms:modified`` al guardar; se fija aquí.
_CORE_DATES = re.compile(rb"(<dcterms:(?:created|modified)\b[^>]*>)[^<]*(</dcterms:(?:created|modified)>)")
MAX_CELL = 32000


def _cell(v: Any) -> Any:
    if v is None or isinstance(v, (int, float, bool)):
        return v
    s = v if isinstance(v, str) else str(v)
    # Una celda que empiece por = + - @ se escribe como texto, nunca como fórmula.
    if s[:1] in ("=", "+", "-", "@"):
        s = "'" + s
    return s[:MAX_CELL]


def workbook_bytes(sheets: Sequence[tuple[str, Sequence[str], Iterable[Sequence[Any]]]]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    wb.remove(wb.active)
    for title, header, rows in sheets:
        ws = wb.create_sheet(title=title[:31])
        ws.append([_cell(h) for h in header])
        for c in ws[1]:
            c.font = Font(bold=True)
        for r in rows:
            ws.append([_cell(v) for v in r])
        ws.freeze_panes = "A2"
    p = wb.properties
    p.creator = "survey-simulator"
    p.lastModifiedBy = None
    p.created = FIXED
    p.modified = FIXED
    buf = io.BytesIO()
    wb.save(buf)
    return normalize_zip(buf.getvalue())


def normalize_zip(data: bytes) -> bytes:
    """Reescribe el ZIP con fechas fijas y el mismo orden de entradas."""
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            zi = zipfile.ZipInfo(info.filename, date_time=(1980, 1, 1, 0, 0, 0))
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o600 << 16
            data_i = src.read(info.filename)
            if info.filename == "docProps/core.xml":
                data_i = _CORE_DATES.sub(rb"\g<1>2000-01-01T00:00:00Z\g<2>", data_i)
            dst.writestr(zi, data_i)
    return out.getvalue()
