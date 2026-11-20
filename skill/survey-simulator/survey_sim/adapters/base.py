"""Contrato de los adaptadores de programas de captura (especificación v1.0, sección 6).

Un adaptador ofrece cuatro operaciones:

- ``detect(data) -> float``: confianza de 0 a 1 de que los bytes son de su formato.
- ``read(data, name) -> RawProgram``: lectura estructural, sin interpretar.
- ``to_ir(raw, semantics) -> ProgramReadResult``: ``Instrument(role=program)`` e informe.
- ``coverage(raw) -> dict``: qué estructuras reconoció, infirió, dejó opacas o sin resolver.

Diferencia con la especificación: las operaciones reciben bytes en lugar de
una ruta. Así el archivo se lee una sola vez, en modo de solo lectura, y el
hash corresponde exactamente a lo analizado. ``read_path`` hace la lectura.

El registro elige el adaptador con mayor confianza. Si ninguno supera el
umbral, el programa se declara no soportado: nunca se fuerza un formato.
"""

from __future__ import annotations

import pathlib
from typing import Any, Optional, Protocol, runtime_checkable

DETECTION_THRESHOLD = 0.5


class UnsupportedProgram(ValueError):
    pass


@runtime_checkable
class Adapter(Protocol):
    name: str
    version: str

    def detect(self, data: bytes) -> float: ...

    def read(self, data: bytes, name: str) -> Any: ...

    def to_ir(self, raw: Any, semantics: Any = None) -> Any: ...

    def coverage(self, raw: Any) -> dict: ...


_REGISTRY: dict[str, Adapter] = {}


def register(adapter: Adapter) -> None:
    if not isinstance(adapter, Adapter):
        raise TypeError("el adaptador no cumple el contrato")
    _REGISTRY[adapter.name] = adapter


def registered() -> tuple[str, ...]:
    return tuple(sorted(_REGISTRY))


def choose(data: bytes, threshold: float = DETECTION_THRESHOLD) -> Adapter:
    scored = sorted(((a.detect(data), name) for name, a in _REGISTRY.items()), reverse=True)
    if not scored or scored[0][0] < threshold:
        best = f"{scored[0][1]}={scored[0][0]:.2f}" if scored else "ninguno"
        raise UnsupportedProgram(f"ningún adaptador supera el umbral {threshold} (mejor: {best})")
    return _REGISTRY[scored[0][1]]


def read_path(path: str | pathlib.Path) -> tuple[bytes, str]:
    """Lee un archivo en modo de solo lectura. Nunca lo modifica."""
    p = pathlib.Path(path)
    with p.open("rb") as fh:
        return fh.read(), p.name


def get(name: str) -> Optional[Adapter]:
    return _REGISTRY.get(name)
