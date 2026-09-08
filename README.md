# doc-conventer

Conversor de documentos escritos en el lenguaje **SEMILLA** a **CSV** y **JSON**, con integración opcional con el **sistema de tareas on-demand de Canvas**.

> ⚠️ Nombre del proyecto (con typo intencional): `doc-conventer` (léase "doc-converter").

## Capacidades

1. **Procesamiento SEMILLA** — Parseo del lenguaje declarativo SEMILLA a un AST intermedio.
2. **Export CSV** — Aplana campos y bloques en un archivo CSV.
3. **Export JSON** — Emite el AST completo como JSON estructurado.
4. **Sistema de tareas/comandos** — CLI con `commander` para orquestar conversiones.
5. **Integración Canvas** — Registro de tareas on-demand (stub, pendiente de API real).

## Requisitos

- Node.js ≥ 18

## Instalación

```bash
npm install
```

## Uso

```bash
# Convertir a CSV (por defecto)
node src/index.js <documento.semilla>

# Convertir a JSON
node src/index.js <documento.semilla> --format json

# Salida a un directorio específico
node src/index.js <documento.semilla> -o ./salida

# Registrar tarea en Canvas
node src/index.js <documento.semilla> --canvas
```

## Sintaxis SEMILLA (referencia)

```semilla
# Comentario
---
titulo: Mi Documento     # Metadatos entre delimitadores ---
autor: Nombre
---

campo1: valor1            # Campos simples
lista: a, b, c            # Valores separados por coma → array

## Detalles               # Bloque (## Nombre)
clave1: val1
clave2: val2
```

## Estructura del proyecto

```
doc-conventer/
├── .claude/          # Skills y configuraciones específicas
├── docs/             # Arquitectura y decisiones
├── src/
│   ├── index.js      # CLI (commander)
│   ├── convert.js    # Orquestador del pipeline
│   ├── parsers/      # Parseo SEMILLA → AST
│   ├── exporters/    # Exportadores CSV / JSON
│   └── canvas/       # Integración con Canvas
├── tests/            # Pruebas unitarias (node:test)
├── .env.example      # Plantilla de variables de entorno
└── data/             # Datos persistentes (si aplica)
```

## Scripts

| Comando | Descripción |
|---|---|
| `npm start` | Ejecuta el CLI |
| `npm test` | Ejecuta pruebas unitarias |
| `npm run test:watch` | Pruebas en modo watch |
| `npm run lint` | Lint del código |
| `npm run lint:fix` | Lint con corrección automática |

## Estado

🚧 **Fase inicial.** El parseo SEMILLA y los exportadores CSV/JSON están implementados con sus pruebas. La integración real con Canvas queda pendiente (actualmente es un stub).