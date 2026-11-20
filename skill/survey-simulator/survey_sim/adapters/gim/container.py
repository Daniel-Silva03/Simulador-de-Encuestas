"""Lectura segura del contenedor JSON de un programa GIM.

El archivo se trata como datos. Antes de construir ningún objeto se
comprueban, en este orden:

1. Tamaño en bytes.
2. Codificación: UTF-8, con o sin BOM. No se adivinan otras codificaciones.
3. Profundidad de anidamiento, con un recorrido lineal del texto que no
   usa recursión (así un JSON muy profundo no puede agotar la pila).
4. Sintaxis JSON con ``json.loads``. No admite ``NaN`` ni ``Infinity``.
   Registra las claves duplicadas y conserva la primera aparición.
5. Número total de nodos y longitud de cada cadena.

Si alguna comprobación falla, se lanza ``GimContainerError`` con un código
estable y el archivo no se procesa.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ReaderLimits:
    """Límites explícitos. Todos son configurables."""

    max_bytes: int = 64 * 1024 * 1024
    max_depth: int = 64
    max_nodes: int = 5_000_000
    max_string_chars: int = 2 * 1024 * 1024
    max_pages: int = 5_000
    max_questions: int = 20_000
    max_variables: int = 50_000
    max_codcomp_decoded_bytes: int = 1024 * 1024
    max_codcomp_ratio: int = 200


class GimContainerError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class DuplicateKey:
    path: str
    key: str


@dataclass
class Container:
    data: dict
    sha256: str
    size: int
    had_bom: bool
    max_depth_seen: int
    nodes: int
    duplicate_keys: list[DuplicateKey] = field(default_factory=list)


def _depth_scan(text: str, limit: int) -> int:
    """Profundidad máxima de ``{``/``[`` fuera de cadenas, sin recursión."""
    depth = deepest = 0
    in_str = esc = False
    for ch in text:
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch in "{[":
            depth += 1
            if depth > deepest:
                deepest = depth
                if deepest > limit:
                    raise GimContainerError("max_depth", f"anidamiento mayor que {limit}")
        elif ch in "}]":
            depth -= 1
    return deepest


def _reject_constant(name: str) -> Any:
    raise GimContainerError("invalid_number", f"valor numérico no permitido: {name}")


def load_container(data: bytes, limits: ReaderLimits = ReaderLimits()) -> Container:
    if not isinstance(data, (bytes, bytearray)):
        raise GimContainerError("not_bytes", "se esperan bytes")
    if len(data) > limits.max_bytes:
        raise GimContainerError("max_bytes", f"{len(data)} bytes; el límite es {limits.max_bytes}")
    sha = hashlib.sha256(data).hexdigest()
    had_bom = data.startswith(b"\xef\xbb\xbf")
    try:
        text = bytes(data).decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise GimContainerError("encoding", f"el archivo no es UTF-8 válido (byte {e.start})") from None
    if not text.strip():
        raise GimContainerError("empty", "archivo vacío")
    depth = _depth_scan(text, limits.max_depth)

    duplicates: list[DuplicateKey] = []

    def pairs_hook(pairs):
        out: dict = {}
        for k, v in pairs:
            if k in out:
                duplicates.append(DuplicateKey(path="", key=k))  # json no informa la ruta
                continue  # se conserva la primera aparición
            out[k] = v
        return out

    try:
        obj = json.loads(text, object_pairs_hook=pairs_hook, parse_constant=_reject_constant)
    except GimContainerError:
        raise
    except json.JSONDecodeError as e:
        code = "truncated" if e.pos >= len(text.rstrip()) else "invalid_json"
        raise GimContainerError(code, f"{e.msg} (línea {e.lineno}, columna {e.colno})") from None
    except RecursionError:  # defensa adicional; la comprobación de profundidad lo evita
        raise GimContainerError("max_depth", "anidamiento excesivo") from None
    if not isinstance(obj, dict):
        raise GimContainerError("not_object", "la raíz del programa debe ser un objeto JSON")

    nodes = 0
    stack: list[Any] = [obj]
    while stack:
        x = stack.pop()
        nodes += 1
        if nodes > limits.max_nodes:
            raise GimContainerError("max_nodes", f"más de {limits.max_nodes} nodos")
        if isinstance(x, dict):
            stack.extend(x.values())
        elif isinstance(x, list):
            stack.extend(x)
        elif isinstance(x, str) and len(x) > limits.max_string_chars:
            raise GimContainerError("max_string", f"cadena de {len(x)} caracteres; el límite es {limits.max_string_chars}")

    return Container(
        data=obj,
        sha256=sha,
        size=len(data),
        had_bom=had_bom,
        max_depth_seen=depth,
        nodes=nodes,
        duplicate_keys=duplicates,
    )
