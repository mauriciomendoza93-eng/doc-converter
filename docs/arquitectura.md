# Arquitectura de doc-conventer

## Visión general (big picture)

El proyecto sigue un **pipeline unidireccional de conversión** con tres etapas bien separadas:

```
Fuente SEMILLA (.semilla)
        │
        ▼
[1] parsers/semilla.js   →  AST intermedio (estructura en memoria)
        │
        ▼
[2] exporters/{csv,json}.js →  Archivo de salida
        │
        ▼
[3] canvas/task.js       →  Registro de tarea on-demand (opcional)
```

El contrato clave es el **AST intermedio** (ver abajo), que desacopla el
parser de los exportadores. Añadir un nuevo formato de salida implica crear
un exportador que consuma ese AST, sin tocar el parser.

## AST intermedio (contrato)

```js
{
  meta:    { [clave]: string },            // Metadatos entre ---
  campos:  { [clave]: string | string[] }, // Campos principales
  bloques: Array<{
    nombre: string,                        // Título "## Nombre"
    items: Array<{ clave: string, valor: string }>
  }>
}
```

## Decisiones

- **ESM (`type: "module"`)** — Uso de `import`/`export` moderno.
- **`node:test`** — Pruebas sin dependencia externa (runner nativo).
- **`commander`** — CLI declarativo para la interfaz de línea de comandos.
- **Canvas como stub** — La integración externa se modela por contrato y se
  sustituye luego por la API real, manteniendo los tests aislados.

## Pendiente

- API real de Canvas (URL y autenticación vía `CANVAS_API_*`).
- Carga de variables de entorno (`.env`) en el runtime.
- Especificación formal y completa del lenguaje SEMILLA.