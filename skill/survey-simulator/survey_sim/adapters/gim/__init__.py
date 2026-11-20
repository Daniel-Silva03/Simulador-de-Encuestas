"""Adaptador GIM JSON: lector estructural (paso 3). El parser de expresiones es el paso 4."""

from __future__ import annotations

import json

from ..base import register
from .container import GimContainerError, ReaderLimits, load_container  # noqa: F401
from .reader import RawProgram, read_program
from .semantics import Semantics, load_semantics  # noqa: F401
from .to_ir import ADAPTER, ADAPTER_VERSION, GimReadReport, ProgramReadResult, to_ir  # noqa: F401

#: Claves de primer nivel características del formato observado.
_SIGNATURE = ("paginas", "estudio", "flujo", "incidencias", "idiomas", "versiones")


class GimAdapter:
    name = ADAPTER
    version = ADAPTER_VERSION

    def __init__(self, limits: ReaderLimits = ReaderLimits()):
        self.limits = limits

    def detect(self, data: bytes) -> float:
        """Confianza por presencia de claves características. Nunca lanza."""
        if not isinstance(data, (bytes, bytearray)) or len(data) > self.limits.max_bytes:
            return 0.0
        try:
            head = bytes(data).decode("utf-8-sig")
        except UnicodeDecodeError:
            return 0.0
        if not head.lstrip().startswith("{"):
            return 0.0
        try:
            obj = load_container(data, self.limits).data
        except (GimContainerError, json.JSONDecodeError, ValueError):
            return 0.0
        if not isinstance(obj.get("paginas"), list):
            return 0.0
        return sum(1 for k in _SIGNATURE if k in obj) / len(_SIGNATURE)

    def read(self, data: bytes, name: str) -> RawProgram:
        return read_program(data, name, self.limits)

    def to_ir(self, raw: RawProgram, semantics: Semantics | None = None, *, hmac_key: bytes | None = None) -> ProgramReadResult:
        return to_ir(raw, semantics, hmac_key=hmac_key)

    def coverage(self, raw: RawProgram) -> dict:
        return {
            "pages": len(raw.pages),
            "variables": len(raw.all_vars()),
            "expressions": len(raw.expressions),
            "codcomp": len(raw.codcomp),
            "issues": len(raw.issues),
            "raw_extra": len(raw.raw_extra),
        }


def read_gim_bytes(data: bytes, name: str, semantics: Semantics | None = None, limits: ReaderLimits = ReaderLimits(),
                   *, hmac_key: bytes | None = None) -> ProgramReadResult:
    """``hmac_key`` (32 bytes, local) solo afecta a las notas de campos privados (v1.1, §8.1)."""
    a = GimAdapter(limits)
    return a.to_ir(a.read(data, name), semantics, hmac_key=hmac_key)


register(GimAdapter())
