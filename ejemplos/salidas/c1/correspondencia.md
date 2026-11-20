# Informe de correspondencia

- Diseño: `transporte_cuestionario.docx` · SHA-256 `ccf0a6e248c6e5fef1f0a36dd554deeb4f0a49945a189ecf837f8efb8f0aacde`
- Programa: `transporte_programa_discrepante.gim` · SHA-256 `31890ad5ab8be5f2e447cf8b32a08e6df845f83595a555a025ef07a24403b1fe`
- Versión de reglas: 1.1.0

## Resultado: CORRESPONDENCIA PROVISIONAL

- Veredicto: **MISMO** (fuente: table; fila: coinciden_estudio_o_instrumento)
- Respaldo: inferred · revisión de observaciones: review_required
- Comparación habilitada: False · emparejamiento alto (0.75)

## Revisión necesaria

- `ed0bd72b1f21f9a5` · study_code · design · inferred_observation → verify_value_manually
- `74095713c205a3b9` · study_code · program · inferred_observation → verify_value_manually
- `828728690457f893` · instrument_id · design · incomplete_search_scope → confirm_search_scope
- `4d736110591c6555` · instrument_id · program · incomplete_search_scope → confirm_search_scope
- `c7f65924f4335432` · wave_label · design · heuristic_absence → verify_absence_manually
- `f1f27b55adf16d38` · wave_label · design · incomplete_search_scope → confirm_search_scope
- `f0293a1fc20d1278` · wave_label · program · heuristic_absence → verify_absence_manually
- `eb6385f8ca853e98` · wave_label · program · incomplete_search_scope → confirm_search_scope
- `2a07bc99288909e4` · questionnaire_version · design · heuristic_absence → verify_absence_manually
- `5f836f0bc1dafa30` · questionnaire_version · design · incomplete_search_scope → confirm_search_scope
- `5a29700e3cc6da19` · questionnaire_version · program · heuristic_absence → verify_absence_manually
- `866acccebee8057a` · questionnaire_version · program · incomplete_search_scope → confirm_search_scope
- `12e2b3c15054c5d5` · phase_label · design · heuristic_absence → verify_absence_manually
- `c52d39466ad04d19` · phase_label · design · incomplete_search_scope → confirm_search_scope
- `7cc4d6f3cab339c2` · phase_label · program · heuristic_absence → verify_absence_manually
- `ac258fa3eaeca461` · phase_label · program · incomplete_search_scope → confirm_search_scope

Asignaciones usadas **no confirmadas**: study

## Emparejamiento (diagnóstico)

- Parejas: 3 · solo diseño: 1 · solo programa: 1 · ambiguas: 0
- Estados: evidencia_exacta: 3

## Hallazgos

- No hay comparación: la identidad no la habilita (v1.0, 7.3).

## Limitaciones

- Todas las asignaciones de identificadores y todas las hipótesis GIM están en inferred: sin declaración humana vigente no se habilita la comparación.
- Las reglas GIM dependen de semántica no confirmada (D15): sus hallazgos son, como máximo, sospecha.
- «Equivalente» exige pareja declarada y comparación habilitada (v1.1, §3.2 y §5.1); con valores vacíos sin política confirmada la enumeración da U y el resultado es no verificable.
- Los umbrales de emparejamiento están calibrados con pares sintéticos (v1.0, 7.2).
- Las salidas no contienen texto del instrumento salvo con --include-text.

_La evidencia de parejas de preguntas nunca prueba la identidad del estudio._
