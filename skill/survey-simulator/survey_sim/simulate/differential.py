"""Simulación diferencial diseño ↔ programa (v1.1, §7.8) e informe consolidado (E3).

Procedimiento:

1. Solo se ejecuta si la comparación está habilitada (identidad, v1.0 7.1–7.3)
   y solo sobre parejas ``declarada`` (v1.1, §3.2). Sin eso, el informe lo
   dice y no hay divergencias.
2. Cada agente recorre el **diseño** con su semilla (escenario declarado o
   uniforme). Sus respuestas a las preguntas emparejadas se trasladan al
   **programa** como guion (mismos códigos; las equivalencias por variable de
   §4 se aplican en el evaluador del programa).
3. Para cada pareja y agente, cada lado está en uno de tres estados:
   ``presentada`` (respondida o sin respuesta en el guion), ``no_presentada``
   (filtro, salto o terminación) o ``sin_decidir`` (``NO_SIMULADO``: el agente
   se interrumpió antes). Solo se compara cuando **ambos lados decidieron**:
   así nunca hay divergencias sobre segmentos no confirmados.
4. En modo estándar solo se ejecutan reglas explícitas, así que todo lo
   decidido está confirmado. En modo experimental, una divergencia es
   HIPOTÉTICA solo si alguno de los dos lados usó una hipótesis **hasta decidir
   esa pregunta** (``AgentResult.hyp_at``); se informa la lista exacta.
5. Un código respondido en el diseño que en el programa existe pero está oculto
   por una regla de presentación de opciones es ``opcion_oculta``, nunca
   ``traslado`` (código inexistente).

Las reglas de ejecución, interrupción y U son las del motor (§7.1–7.3), sin
cambios. Nada de esto prueba qué hará el programa con encuestados reales.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

from survey_sim.correspondence.records import Equivalences, NO_EQUIVALENCES
from survey_sim.ir.models import Instrument

from .engine import EXPERIMENTAL, INTERRUPTED, NO_SIMULADO, TERMINATED, AgentResult, Simulator
from .scenarios import FUNCTIONAL, Scenario

SHOWN, NOT_SHOWN, UNDECIDED = "presentada", "no_presentada", "sin_decidir"
_SHOWN_STATES = {"VALID", "MISSING_CODE", "UNANSWERED", "UNKNOWN_TO_EVALUATOR", "NO_CALCULADO"}
EXAMPLES = 5

DISCLAIMER = ("Simulación diferencial con datos sintéticos: compara los recorridos del diseño y del programa "
              "con las mismas respuestas, solo donde ambos lados están decididos. No es una observación ni "
              "una estimación, y no prueba qué hará el programa con encuestados reales.")


def cell_state(a: AgentResult, item: str) -> str:
    c = a.cells.get(item)
    if c is None or c.state == NO_SIMULADO:
        return UNDECIDED
    if c.state == "NOT_APPLICABLE":
        return NOT_SHOWN
    if c.state in _SHOWN_STATES:
        return SHOWN
    return UNDECIDED


@dataclass
class Divergence:
    category: str  # presentacion | terminacion | traslado | opcion_oculta
    design: Optional[str]
    program: Optional[str]
    design_state: str
    program_state: str
    agents: list[int] = field(default_factory=list)
    hypothetical_agents: list[int] = field(default_factory=list)
    hypotheses: set[str] = field(default_factory=set)

    def add(self, agent: int, hyps) -> None:
        self.agents.append(agent)
        if hyps:
            self.hypothetical_agents.append(agent)
            self.hypotheses.update(hyps)

    def to_dict(self) -> dict:
        n, h = len(self.agents), len(self.hypothetical_agents)
        return {
            "category": self.category, "design": self.design, "program": self.program,
            "design_state": self.design_state, "program_state": self.program_state,
            "agents": n, "confirmed_agents": n - h, "hypothetical_agents": h,
            "HIPOTETICO": h > 0, "hypotheses": sorted(self.hypotheses),
            "example_agents": self.agents[:EXAMPLES],
            "evidence": "confirmada" if h == 0 else ("hipotetica" if h == n else "mixta"),
        }


@dataclass
class DifferentialResult:
    enabled: bool
    reason: Optional[str]
    mode: str
    seed: int
    n_agents: int
    scenario: Scenario
    pairs: list[tuple[str, str]]
    design_run: Optional[object] = None
    program_run: Optional[object] = None
    divergences: list[Divergence] = field(default_factory=list)
    comparable: dict = field(default_factory=dict)
    statuses: dict = field(default_factory=dict)
    transfer_failures: int = 0

    @property
    def hypothetical(self) -> bool:
        return self.mode == EXPERIMENTAL

    def to_dict(self) -> dict:
        divs = [d.to_dict() for d in self.divergences]
        return {
            "enabled": self.enabled, "reason": self.reason, "mode": self.mode, "seed": self.seed,
            "agents": self.n_agents, "SINTETICO": True, "HIPOTETICO": self.hypothetical,
            "scenario": {"name": self.scenario.name, "label": self.scenario.label, "assumptions": self.scenario.assumptions,
                         "sha256": self.scenario.sha256},
            "declared_pairs": [{"design": d, "program": p} for d, p in self.pairs],
            "agent_statuses": self.statuses, "transfer_failures": self.transfer_failures,
            "comparable": self.comparable,
            "divergences": divs,
            "divergences_by_evidence": dict(Counter(d["evidence"] for d in divs)),
            "disclaimer": DISCLAIMER,
        }


def _not_enabled(reason: str, mode: str, seed: int, n: int, sc: Scenario, pairs) -> DifferentialResult:
    return DifferentialResult(False, reason, mode, seed, n, sc, list(pairs))


def run_differential(design: Instrument, program: Instrument, *, declared_pairs: list[tuple[str, str]],
                     comparison_enabled: bool, mode: str, scenario: Scenario, seed: int, n_agents: int,
                     eq: Equivalences = NO_EQUIVALENCES) -> DifferentialResult:
    pairs = sorted(declared_pairs)
    if not comparison_enabled:
        return _not_enabled("comparación no habilitada (identidad, v1.0 7.1–7.3)", mode, seed, n_agents, scenario, pairs)
    if not pairs:
        return _not_enabled("sin parejas declaradas (v1.1, §3.2)", mode, seed, n_agents, scenario, pairs)
    if scenario.mode == FUNCTIONAL:
        return _not_enabled("el diferencial usa un escenario de respuestas, no un guion", mode, seed, n_agents, scenario, pairs)

    drun = Simulator(design, mode=mode, scenario=scenario, seed=seed, eq=eq).run(n_agents)
    d2p = dict(pairs)
    cases = []
    for a in drun.agents:
        resp = {}
        for d, p in pairs:
            c = a.cells.get(d)
            if c is not None and c.state in ("VALID", "MISSING_CODE") and isinstance(c.value, str):
                codes = tuple(c.value.split(";"))
                resp[p] = codes if len(codes) > 1 else codes[0]
        cases.append({"nombre": f"agente_{a.index}", "respuestas": resp})
    replay = Scenario(FUNCTIONAL, "respuestas del diseño reproducidas en el programa", scenario.assumptions, scenario.sha256,
                      False, {}, cases, ())
    prun = Simulator(program, mode=mode, scenario=replay, seed=seed, eq=eq).run(len(cases))

    by_key: dict[tuple, Divergence] = {}
    comparable: dict[str, dict] = defaultdict(lambda: {"comparable": 0, "design_undecided": 0, "program_undecided": 0})
    failures = 0
    p2d = {p: d for d, p in pairs}
    for da, pa in zip(drun.agents, prun.agents):
        intr = pa.interruption or {}
        if pa.status == INTERRUPTED and intr.get("rule") == "guion":
            pi = intr.get("item")
            di = p2d.get(pi)
            # Hipótesis: las usadas en el diseño hasta responder la pregunta y en el programa hasta ese punto.
            hyps = set(da.hyp_at.get(di, ())) | set(pa.hypotheses)
            if intr.get("reason") == "codigo_del_guion_oculto":
                cat, pstate = "opcion_oculta", "codigo_oculto_por_regla"
            else:
                cat, pstate = "traslado", "codigo_inexistente"
                failures += 1
            key = (cat, di, pi, "respondida", pstate)
            by_key.setdefault(key, Divergence(cat, di, pi, "respondida", pstate)).add(da.index, sorted(hyps))
        for d, p in pairs:
            sd, sp = cell_state(da, d), cell_state(pa, p)
            stat = comparable[f"{d}|{p}"]
            if sd == UNDECIDED or sp == UNDECIDED:
                stat["design_undecided"] += sd == UNDECIDED
                stat["program_undecided"] += sp == UNDECIDED
                continue
            stat["comparable"] += 1
            if sd != sp:
                hyps = set(da.hyp_at.get(d, ())) | set(pa.hyp_at.get(p, ()))
                key = ("presentacion", d, p, sd, sp)
                by_key.setdefault(key, Divergence("presentacion", d, p, sd, sp)).add(da.index, sorted(hyps))
        if INTERRUPTED not in (da.status, pa.status) and (da.status == TERMINATED) != (pa.status == TERMINATED):
            hyps = set(da.hypotheses) | set(pa.hypotheses)
            key = ("terminacion", None, None, da.status, pa.status)
            by_key.setdefault(key, Divergence("terminacion", None, None, da.status, pa.status)).add(da.index, sorted(hyps))
    divs = sorted(by_key.values(), key=lambda v: (v.category, v.design or "", v.program or "", v.design_state, v.program_state))
    statuses = {"design": dict(sorted(Counter(a.status for a in drun.agents).items())),
                "program": dict(sorted(Counter(a.status for a in prun.agents).items()))}
    return DifferentialResult(True, None, mode, seed, n_agents, scenario, pairs, drun, prun, divs,
                              {k: comparable[k] for k in sorted(comparable)}, statuses, failures)
