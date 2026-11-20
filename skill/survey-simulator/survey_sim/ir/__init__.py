"""Representación intermedia (IR) de Survey Simulator.

Especificación de referencia: ``survey_simulator_spec_v1.0.md``, sección 4.
"""

from .models import IR_VERSION, Instrument  # noqa: F401
from .serialize import from_json_bytes, ir_hash, to_json_bytes  # noqa: F401
from .validate import validate_instrument, validate_or_raise  # noqa: F401
