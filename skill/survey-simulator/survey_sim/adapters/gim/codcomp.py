"""Inspección de ``codcomp`` como datos (nunca como código).

``codcomp`` acompaña a muchas expresiones GIM. En el formato observado a
veces es un número (índice en un depósito), a veces texto plano y a veces
base64 de datos comprimidos con zlib. Este módulo solo:

- decodifica base64 con validación estricta;
- descomprime con zlib, con límite de tamaño de salida y de razón de
  compresión;
- describe el resultado (tamaño, SHA-256, proporción de caracteres
  imprimibles).

No interpreta el contenido, no lo ejecuta y no lo copia a la IR. El
contraste con las expresiones (variables y comparaciones referenciadas) es
trabajo del paso 4.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
import zlib
from dataclasses import dataclass
from typing import Optional

_B64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}$")
#: Longitud a partir de la cual una cadena en alfabeto base64 se trata como codificada.
MIN_ENCODED_LENGTH = 16


@dataclass(frozen=True)
class CodcompInfo:
    status: str  # empty | index | not_encoded | decoded | corrupt_base64 | corrupt_zlib | too_large | unexpected_type
    encoded_length: int
    decoded_length: Optional[int] = None
    decoded_sha256: Optional[str] = None
    printable_ratio: Optional[float] = None
    detail: str = ""


def inspect_codcomp(value: object, *, max_decoded: int, max_ratio: int) -> CodcompInfo:
    if value is None or value == "":
        return CodcompInfo("empty", 0)
    if isinstance(value, bool):
        return CodcompInfo("unexpected_type", 0, detail="bool")
    if isinstance(value, int):
        return CodcompInfo("index", 0, detail=str(value))
    if not isinstance(value, str):
        return CodcompInfo("unexpected_type", 0, detail=type(value).__name__)
    s = value.strip()
    if not _B64.match(s):
        return CodcompInfo("not_encoded", len(value))
    if len(s) % 4 != 0:
        # Alfabeto base64 con longitud imposible: corrupto si es largo; si es
        # corto, puede ser simplemente un identificador.
        if len(s) >= MIN_ENCODED_LENGTH:
            return CodcompInfo("corrupt_base64", len(value), detail="longitud no múltiplo de 4")
        return CodcompInfo("not_encoded", len(value))
    try:
        raw = base64.b64decode(s, validate=True)
    except (binascii.Error, ValueError):
        return CodcompInfo("corrupt_base64", len(value))
    # zlib: cabecera de 2 bytes con CMF/FLG válidos.
    if len(raw) < 2 or (raw[0] & 0x0F) != 8 or ((raw[0] << 8) | raw[1]) % 31 != 0:
        return CodcompInfo("not_encoded", len(value), detail="base64 sin cabecera zlib")
    limit = min(max_decoded, max(1, len(raw)) * max_ratio)
    d = zlib.decompressobj()
    try:
        out = d.decompress(raw, limit)
        if d.unconsumed_tail:
            return CodcompInfo("too_large", len(value), detail=f"supera {limit} bytes descomprimidos")
        out += d.flush()
        if not d.eof:
            return CodcompInfo("corrupt_zlib", len(value), detail="flujo zlib incompleto")
    except zlib.error as e:
        return CodcompInfo("corrupt_zlib", len(value), detail=str(e))
    if len(out) > limit:
        return CodcompInfo("too_large", len(value), detail=f"supera {limit} bytes descomprimidos")
    printable = sum(1 for b in out if 32 <= b < 127 or b in (9, 10, 13))
    return CodcompInfo(
        "decoded",
        len(value),
        decoded_length=len(out),
        decoded_sha256=hashlib.sha256(out).hexdigest(),
        printable_ratio=round(printable / len(out), 4) if out else 1.0,
    )
