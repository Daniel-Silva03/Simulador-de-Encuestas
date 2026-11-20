# Ejemplos

Todo lo que contiene esta carpeta es **sintético**: lo generaron los constructores de prueba del proyecto. No contiene instrumentos reales ni datos de encuestados. El estudio ficticio se identifica como «Encuesta sintética de transporte ZZ900001».

## Entradas (`entradas/`)

| Archivo | Descripción |
|---|---|
| `transporte_cuestionario.docx` | Cuestionario Word de 4 preguntas (P1 a P4), con un salto («CONTINUAR CON P4») y un filtro («APLIQUE SI») |
| `transporte_programa_concordante.gim` | Programa GIM que implementa el cuestionario sin diferencias |
| `transporte_programa_discrepante.gim` | Programa GIM con 5 diferencias introducidas a propósito (ver abajo) |

Diferencias introducidas en el programa discrepante:

1. P2: el código 3 («Todos los días») pasa a ser 4.
2. P4: la opción 1 se llama «Satisfecho» en lugar de «Muy satisfecho».
3. P2: el filtro se aplica cuando P1=2, en lugar de P1=1.
4. P3 no existe en el programa.
5. P5 existe solo en el programa.

## Salidas (`salidas/`)

Son los resultados de ejecutar el flujo completo de [docs/guia_de_uso.md](../docs/guia_de_uso.md) con el cuestionario y el programa discrepante, semilla 2026.

| Carpeta o archivo | Paso | Contenido |
|---|---|---|
| `c1/` | Diagnóstico | Correspondencia sin identidad declarada: comparación no habilitada |
| `review_log.jsonl` | Identidad | Declaración humana (simulada para el ejemplo) |
| `c2/` | Comparación inicial | Plantillas de pareos y equivalencias |
| `pareos.yaml`, `equivalencias.yaml` | Decisiones | Pareos confirmados y equivalencias por variable |
| `c3/` | Comparación final | 2 errores comprobados, 3 sospechas, 2 no verificables |
| `s1/` | Simulación del cuestionario, modo estándar | 100 agentes |
| `s2/` | Simulación del programa, modo experimental | 100 agentes, marca HIPOTÉTICO |
| `consolidado/` | Informe consolidado, modo estándar | 0 divergencias (el programa queda sin decidir) |
| `consolidado_exp/` | Informe consolidado, modo experimental | 1 divergencia hipotética en P2 |

Los archivos `pareos.yaml`, `equivalencias.yaml` y `review_log.jsonl` están vinculados por SHA-256 a estos insumos y a la configuración de la Skill. Con otros archivos no surten efecto.

Para reproducir el ejemplo con el programa sin diferencias, sustituye `transporte_programa_discrepante.gim` por `transporte_programa_concordante.gim`. Tendrás que generar y confirmar tus propios pareos y equivalencias.
