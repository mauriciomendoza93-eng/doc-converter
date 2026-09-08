/**
 * convert.js — Orquestador de conversión.
 *
 * Coordina el pipeline: lectura → parseo SEMILLA → exportación → (opcional) Canvas.
 */
import { readFile } from 'node:fs/promises';
import { parseSemilla } from './parsers/semilla.js';
import { exportCsv } from './exporters/csv.js';
import { exportJson } from './exporters/json.js';
import { createCanvasTask } from './canvas/task.js';

/**
 * Convierte un documento SEMILLA al formato solicitado.
 *
 * @param {string} documento Ruta al archivo fuente SEMILLA.
 * @param {object} options Opciones: { format, out, canvas }.
 * @returns {Promise<{outputPath: string, canvasTaskId?: string}>}
 */
export async function convert(documento, options) {
  const source = await readFile(documento, 'utf8');

  // 1. Parseo del lenguaje SEMILLA → estructura intermedia (AST).
  const ast = parseSemilla(source);

  // 2. Exportación según formato.
  const { format = 'csv', out = './out' } = options;
  let outputPath;
  if (format === 'json') {
    outputPath = await exportJson(ast, out);
  } else {
    outputPath = await exportCsv(ast, out);
  }

  // 3. Integración opcional con Canvas (tareas on-demand).
  let canvasTaskId;
  if (options.canvas) {
    canvasTaskId = await createCanvasTask({ source: documento, outputPath });
  }

  return { outputPath, canvasTaskId };
}