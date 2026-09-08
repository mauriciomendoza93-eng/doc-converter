/**
 * json.js — Exportador a JSON.
 *
 * Emite el AST completo (metadatos, campos y bloques) como JSON estructurado
 * con indentación legible.
 */
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

/**
 * Exporta el AST a un archivo JSON.
 *
 * @param {object} ast AST producido por parseSemilla.
 * @param {string} outDir Directorio de salida.
 * @returns {Promise<string>} Ruta del archivo generado.
 */
export async function exportJson(ast, outDir) {
  await mkdir(outDir, { recursive: true });
  const outputPath = join(outDir, 'documento.json');
  const content = JSON.stringify(ast, null, 2);
  await writeFile(outputPath, content, 'utf8');
  return outputPath;
}