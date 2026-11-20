# Formatos de los archivos del usuario

Todos los archivos llevan un `binding`: los SHA-256 de los insumos y de la configuración y las versiones de los lectores, el parser y el módulo de correspondencia. Si alguno cambia, el archivo queda **no vigente** y deja de surtir efecto. Las plantillas que genera `corresponder` ya traen el `binding` correcto.

## Pareos (`pareos.yaml`)

```yaml
record_type: pareos
binding: { ... }          # copiado de plantilla_pareos.yaml
pares:
  - {design: P1, program: P1, confirmado: true, motivo: "revisado con el equipo"}
  - {design: P2, program: P2, confirmado: false}   # sigue siendo propuesta
```

Solo las parejas con `confirmado: true` pasan a `declarada`.

## Equivalencias (`equivalencias.yaml`)

```yaml
record_type: equivalencias
binding: { ... }          # copiado de plantilla_equivalencias.yaml
variables:
  - lado: program         # design o program
    variable: P1
    pares:
      - {texto: "1", literal: 1}
      - {texto: "2", literal: 2}
```

`texto` es una cadena y `literal` un número. Cada variable se declara por separado.

## Registro de identidad (`review_log.jsonl`)

Se escribe con `declarar-identidad`. Es un archivo de solo adición: un registro nuevo sustituye a otro con `--sustituye <id>`. No se edita a mano.

## Escenario de simulación (`escenario.yaml`)

```yaml
record_type: escenario
nombre: "Uso frecuente"
supuestos: "Escenario artificial: 70 % usa transporte público"
items:
  P1: {"1": 70, "2": 25, "9": 5}
  P4: {"1": 1, "2": 1, "3": 1, "4": 1}
```

Los pesos se normalizan. Los ítems no declarados siguen la distribución uniforme. Un guion de casos usa `record_type: guion` con `casos: [{nombre, respuestas: {P1: "1", ...}}]`.
