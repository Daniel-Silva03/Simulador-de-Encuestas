"""Política de privacidad de los informes (AUT-1, decisiones P-1 y P-2).

- Los informes son vistas derivadas y minimizadas de la IR: por defecto no
  incluyen texto libre.
- Para un texto se registra su longitud y, si hay una clave local
  configurada, su HMAC-SHA256. Sin clave no hay hash: el modo es ``none``.
- Nunca se usa SHA-256 sin clave para «anonimizar» texto libre: un texto
  corto o predecible se adivina probando candidatos.
- La clave tiene exactamente 32 bytes, vive fuera del repositorio y nunca se
  escribe en informes, registros ni excepciones. De ella solo se publica una
  huella (primeros 16 caracteres hexadecimales de su SHA-256), que no permite
  recuperarla.

Las mismas entradas y la misma clave producen siempre las mismas salidas.
Salidas generadas con claves distintas no son comparables entre sí; la huella
permite saberlo.
"""

from __future__ import annotations

import hashlib
import hmac
import pathlib
from dataclasses import dataclass, field
from typing import Optional

KEY_BYTES = 32
FINGERPRINT_CHARS = 16


class PrivacyError(ValueError):
    """Error de configuración de privacidad. Su mensaje nunca contiene la clave."""


@dataclass(frozen=True)
class TextProtector:
    """Describe textos sin copiarlos. La clave no aparece en ``repr`` ni en errores."""

    key: Optional[bytes] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.key is not None:
            if not isinstance(self.key, (bytes, bytearray)):
                raise PrivacyError("la clave debe ser bytes")
            if len(self.key) != KEY_BYTES:
                raise PrivacyError(f"la clave debe tener exactamente {KEY_BYTES} bytes")

    @property
    def mode(self) -> str:
        return "hmac" if self.key is not None else "none"

    @property
    def key_fingerprint(self) -> Optional[str]:
        if self.key is None:
            return None
        return hashlib.sha256(bytes(self.key)).hexdigest()[:FINGERPRINT_CHARS]

    def digest(self, text: str) -> Optional[str]:
        """HMAC-SHA256 del texto exacto en UTF-8, o ``None`` sin clave."""
        if self.key is None:
            return None
        return hmac.new(bytes(self.key), text.encode("utf-8"), hashlib.sha256).hexdigest()

    def describe(self, text: str) -> dict:
        """Longitud y, si hay clave, HMAC. Nunca el contenido."""
        out: dict = {"length": len(text)}
        h = self.digest(text)
        if h is not None:
            out["hmac_sha256"] = h
        return out

    def report_block(self, include_text: bool) -> dict:
        """Bloque ``privacy`` del informe: modo, huella y si se incluyó texto libre."""
        return {
            "mode": self.mode,
            "key_fingerprint": self.key_fingerprint,
            "include_text": bool(include_text),
            "policy": "AUT-1: informes minimizados; HMAC-SHA256 solo con clave local; sin clave, sin hash",
        }


NO_KEY = TextProtector()


def load_key_file(path: str | pathlib.Path) -> bytes:
    """Lee una clave de 32 bytes desde un archivo local. No revela su contenido en errores."""
    p = pathlib.Path(path)
    try:
        data = p.read_bytes()
    except OSError:
        raise PrivacyError("no se pudo leer el archivo de clave") from None
    if len(data) != KEY_BYTES:
        raise PrivacyError(f"el archivo de clave debe contener exactamente {KEY_BYTES} bytes")
    return data
