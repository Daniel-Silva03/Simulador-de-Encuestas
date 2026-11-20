# Limitaciones conocidas

- **Semántica GIM no confirmada.** Todas las hipótesis del registro `config/semantics/gim.yaml` están en `inferred`. Por eso:
  - los hallazgos sobre reglas GIM son, como máximo, «sospecha»;
  - la simulación de un programa GIM en modo estándar se interrumpe en la primera regla de flujo GIM (v1.1, §7.5). La política de interrupción no se relaja para aumentar la cobertura.
- **Reglas opacas.** Las condiciones con funciones, `O`, conjuntos con comas, aritmética y los destinos especiales `@…@` son opacas. No se ejecutan nunca y producen «no verificable».
- **Identidad del estudio.** Las asignaciones de identificadores son inferidas. La comparación solo se habilita con una declaración humana registrada y vigente. La evidencia de parejas de preguntas nunca prueba la identidad.
- **Equivalencia de condiciones.** Solo hay dos vías: identidad estructural o enumeración completa de un dominio finito, siempre con pareja declarada y comparación habilitada. Cuando el dominio no es finito, hay valores U o se alcanza el límite de enumeración, el resultado es «no verificable».
- **Tipos.** No existe coerción general entre texto y número. Hace falta una equivalencia declarada por variable.
- **Simulación.** Los datos son sintéticos y los escenarios, artificiales. No se simulan las dependencias externas (GPS, tablas, cuotas).
- **Árbol de recorridos.** Es estructural: no prueba que los recorridos ocurran realmente en el programa.
- **Hallazgos de la auditoría:** H-03 se resuelve en el comparador y el simulador mediante equivalencias declaradas; H-04 está corregido en la v1.1; H-08, H-09 y H-15 están resueltos en los lectores (v1.1, §8): los campos `intro` y `svarenlacecod` del programa solo se registran por presencia, longitud y HMAC con clave; los avisos del validador GIM quedan en `unresolved`; los dígitos que no son 0-9 no se leen como códigos y se registran como `non_ascii_digit`.
- **Simulación diferencial.** Solo compara parejas declaradas y tramos decididos en ambos lados. Una divergencia es HIPOTÉTICA cuando algún lado usó una hipótesis hasta decidir esa pregunta.
- **Validación.** No hay validación con instrumentos reales: solo con instrumentos sintéticos.
- **Entorno.** Cada comando de Claude tiene un límite de tiempo (120 s por defecto). Para simulaciones grandes, divide el número de agentes en varias llamadas con semillas distintas.
