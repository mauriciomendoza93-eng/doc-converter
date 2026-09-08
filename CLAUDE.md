# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Proyecto

`doc-conventer` — conversor de documentos escritos en el lenguaje **SEMILLA** a **CSV** y **JSON**, con integración opcional con el **sistema de tareas on-demand de Canvas**. (El nombre contiene un typo intencional: "conventer".)

## Comandos

```bash
npm install               # Instalar dependencias
node src/index.js <doc>   # Ejecutar el CLI (por defecto exporta CSV)
npm test                  # Pruebas unitarias (node:test)
npm run test:watch        # Pruebas en modo watch
npm run lint              # ESLint
```

**Prueba individual:**

```bash
node --test tests/parsers/semilla.test.js
```

**Ejecutar el CLI con opciones:**

```bash
node src/index.js <documento.semilla> --format json --out ./salida --canvas
```

## Arquitectura (big picture)

El proyecto es un **pipeline unidireccional de conversión**:

```
Fuente SEMILLA → ast (parsers) → archivo (exporters) → [canvas]
```

La pieza central es el **AST intermedio** (contrato entre parser y exportadores):

```js
{
  meta:    { [clave]: string },            // Metadatos entre ---
  campos:  { [clave]: string | string[] }, // Campos principales
  bloques: Array<{ nombre, items: Array<{ clave, valor }> }>
}
```

**Archivos clave a leer para entender el flujo:**

- `src/index.js` — CLI con `commander`, punto de entrada.
- `src/convert.js` — orquestador: `readFile → parseSemilla → export → canvas`.
- `src/parsers/semilla.js` — parseo de la sintaxis SEMILLA → AST.
- `src/exporters/{csv,json}.js` — consumidores del AST.
- `src/canvas/task.js` — stub de integración (contrato definido, API real pendiente).

**Decisiones estructurales:**

- **ESM** (`"type": "module"`) en todo el proyecto.
- **Pruebas con `node:test`** — sin dependencia de test-runner externo.
- **Canvas es un stub** — la integración externa se modela por contrato (`createCanvasTask({ source, outputPath }) → taskId`), no por llamada real. Sustituir el stub sin cambiar el contrato.
- Añadir un nuevo formato de salida = **crear un exportador** que consuma el AST, sin tocar el parser.

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

Ver `docs/arquitectura.md` para el detalle del contrato y las decisiones de diseño.

## Estado

🚧 **Fase inicial.** Parser SEMILLA y exportadores CSV/JSON implementados con pruebas. Integración real de Canvas y carga de `.env` pendientes.