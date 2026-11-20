"""Registro de identificadores de la IR del programa GIM (AUT-2, H-02).

Asigna identificadores únicos a variables y grillas sin abortar la lectura
cuando dos elementos piden el mismo nombre.

Reglas:

- **Reserva.** Todos los nombres originales de variable del documento se
  reservan antes de asignar nada: son los nombres que usan las condiciones
  y los destinos de salto.
- **Variables**, en orden del documento. La primera aparición de un nombre
  lo conserva. La aparición ``k`` (``k >= 2``) prueba ``nom#k``,
  ``nom#(k+1)``… y toma el primer candidato libre.
- **Grillas**, después de todas las variables y en orden del documento.
  Prueban ``base``, ``base#grid``, ``base#grid#2``, ``base#grid#3``… y
  toman el primer candidato libre.
- Un candidato está libre si no es un nombre original reservado ni un
  identificador ya asignado. Cada candidato se comprueba antes de asignarlo.

El registro es puro y determinista: no tiene estado global, no usa
``hash()`` y el resultado solo depende del orden de las llamadas. Solo
asigna identificadores; quien lo usa decide qué diagnósticos registrar.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

#: Clase de lo que ocupa un candidato rechazado.
RESERVED_VARIABLE = "nombre original de variable"
DERIVED_VARIABLE = "identificador derivado de variable"
GRID = "grilla"


@dataclass(frozen=True)
class Rejected:
    """Candidato ocupado: el identificador y qué lo ocupa."""

    candidate: str
    occupied_by: str


@dataclass(frozen=True)
class Assignment:
    """Resultado de una asignación."""

    kind: str  # "variable" o "grid"
    base: str
    assigned: str
    occurrence: int = 1  # solo para variables
    rejected: tuple[Rejected, ...] = ()

    @property
    def derived(self) -> bool:
        """El identificador asignado no es el nombre base."""
        return self.assigned != self.base


class IdRegistry:
    """Registro de identificadores de una sola lectura."""

    def __init__(self, reserved_variable_names: Iterable[str]):
        self._reserved = frozenset(reserved_variable_names)
        self._owner: dict[str, str] = {}
        self._occurrences: dict[str, int] = {}
        self._grids_started = False

    def occupied_by(self, candidate: str) -> str | None:
        """Qué ocupa ``candidate``, o ``None`` si está libre."""
        if candidate in self._owner:
            return self._owner[candidate]
        if candidate in self._reserved:
            return RESERVED_VARIABLE
        return None

    def _take(self, candidate: str, owner: str) -> None:
        # Defensa: nunca se asigna dos veces el mismo identificador.
        if candidate in self._owner:
            raise AssertionError(f"identificador ya asignado: {candidate!r}")
        self._owner[candidate] = owner

    def assign_variable(self, nom: str) -> Assignment:
        if self._grids_started:
            raise RuntimeError("las variables se asignan antes que las grillas")
        if nom not in self._reserved:
            raise ValueError(f"nombre de variable no reservado: {nom!r}")
        n = self._occurrences.get(nom, 0) + 1
        self._occurrences[nom] = n
        if n == 1:
            # Los derivados nunca toman un nombre reservado, así que la
            # primera aparición siempre lo encuentra libre.
            self._take(nom, RESERVED_VARIABLE)
            return Assignment("variable", nom, nom, 1)
        rejected: list[Rejected] = []
        k = n
        while True:
            cand = f"{nom}#{k}"
            occ = self.occupied_by(cand)
            if occ is None:
                self._take(cand, DERIVED_VARIABLE)
                return Assignment("variable", nom, cand, n, tuple(rejected))
            rejected.append(Rejected(cand, occ))
            k += 1

    def assign_grid(self, base: str) -> Assignment:
        self._grids_started = True
        rejected: list[Rejected] = []
        k = 1
        while True:
            cand = base if k == 1 else (f"{base}#grid" if k == 2 else f"{base}#grid#{k - 1}")
            occ = self.occupied_by(cand)
            if occ is None:
                self._take(cand, GRID)
                return Assignment("grid", base, cand, 1, tuple(rejected))
            rejected.append(Rejected(cand, occ))
            k += 1


def describe(a: Assignment) -> str:
    """Detalle legible para ``UnresolvedEntry.detail``.

    Se redacta como asignación resuelta, no como error de lectura: el
    elemento se conserva con el identificador indicado.
    """
    who = "grilla (tabitems)" if a.kind == "grid" else f"variable (aparición {a.occurrence} de «{a.base}»)"
    taken = "; ".join(f"«{r.candidate}» ocupado por {r.occupied_by}" for r in a.rejected)
    return (f"{who}: nombre base «{a.base}»; candidatos ocupados: {taken}; "
            f"identificador asignado «{a.assigned}». Asignación resuelta; el elemento se conserva y su etiqueta mantiene el nombre original.")
