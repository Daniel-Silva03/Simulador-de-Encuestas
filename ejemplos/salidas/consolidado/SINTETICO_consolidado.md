# Informe consolidado

**SINTÉTICO**: Contiene resultados de simulación sintética. No son observaciones, resultados ni estimaciones reales.

- Diseño: `transporte_cuestionario.docx` · `ccf0a6e248c6e5fe…`
- Programa: `transporte_programa_discrepante.gim` · `31890ad5ab8be5f2…`

## Correspondencia: CORRESPONDEN

- Identidad: MISMO · comparación habilitada: True
- Parejas: {'declarada': 3} · solo diseño: ['P3'] · solo programa: ['P5']
- Hallazgos: error comprobado 2, sospecha 3, no verificable 2
- Condiciones: diferente 1, equivalente 1

## Simulación diferencial (estandar, semilla 2026, 200 agentes)

- Escenario: SINTÉTICO – PRUEBA DE PROCESAMIENTO (escenario artificial uniforme)
- Estados de los agentes: diseño {'completado': 67, 'interrumpido_indeterminado': 133} · programa {'interrumpido_indeterminado': 200}
- Sin divergencias en los segmentos comparables.
- Simulación diferencial con datos sintéticos: compara los recorridos del diseño y del programa con las mismas respuestas, solo donde ambos lados están decididos. No es una observación ni una estimación, y no prueba qué hará el programa con encuestados reales.

## Limitaciones

- Todas las asignaciones de identificadores y todas las hipótesis GIM están en inferred: sin declaración humana vigente no se habilita la comparación.
- Las reglas GIM dependen de semántica no confirmada (D15): sus hallazgos son, como máximo, sospecha.
- «Equivalente» exige pareja declarada y comparación habilitada (v1.1, §3.2 y §5.1); con valores vacíos sin política confirmada la enumeración da U y el resultado es no verificable.
- Los umbrales de emparejamiento están calibrados con pares sintéticos (v1.0, 7.2).
- Las salidas no contienen texto del instrumento salvo con --include-text.
- Validaciones (controles) y cálculos no se comparan en esta entrega.
- Las incidencias GIM no producen terminaciones (v1.1, §2.2): la terminación solo se compara como no verificable.
- Los datos de la simulación diferencial son sintéticos: no son observaciones ni estimaciones.
- Solo se comparan parejas declaradas con comparación habilitada, y solo donde ambos lados decidieron el recorrido.
- En modo estándar las reglas GIM (todas inferidas) interrumpen al agente del programa en la primera regla de flujo (v1.1, §7.5): la cobertura comparable puede ser pequeña.
- En modo experimental las divergencias de agentes que usaron hipótesis se marcan HIPOTÉTICO; ninguna confirma la semántica de esas reglas.
- Una divergencia es HIPOTÉTICA solo si algún lado usó una hipótesis hasta decidir esa pregunta; se informa la lista exacta.
- Un código oculto en el programa por una regla de presentación de opciones se informa como opcion_oculta, nunca como código inexistente.
- El árbol de recorridos y las divergencias no prueban qué hará el programa con encuestados reales.
