/**
 * csv.js — Exportador a CSV.
 *
 * Convierte el AST intermedio a un archivo CSV. Los campos de nivel superior
 * se convierten en una fila principal; los bloques se emiten como filas
 * adicionales agrupadas por su nombre.
 */
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

/**
 * Exporta el AST a un archivo CSV.
 *
 * @param {object} ast AST producido por parseSemilla.
 * @param {string} outDir Directorio de salida.
 * @returns {Promise<string>} Ruta del archivo generado.
 */
export async function exportCsv(ast, outDir) {
  const rows = [];

  // Encabezados: claves de campos + un prefijo para bloques.
  const allCols = [...Object.keys(ast.campos)];
  for (const bloque of ast.bloques) {
    allCols.push(`${bloque.nombre}:clave`, `${bloque.nombre}:valor`);
  }

  const esc = (v) => JSON.stringify(v ?? ''); // Escapado CSV mínimo.
  const header = allCols.join(',');
  rows.push(header);

  const baseRow = Object.fromEntries(Object.entries(ast.campos).map(([k, v]) => [k, Array.isArray(v) ? v.join('|') : v]));
  const maxBlockItems = Math.max(0, ...ast.bloques.map((b) => b.items.length));

  for (let i = 0; i < Math.max(1, maxBlockItems); i++) {
    const row = { ...baseRow };
    for (const bloque of ast.bloques) {
      const item = bloque.items[i];
      row[`${bloque.nombre}:clave`] = item?.clave ?? '';
      row[`${bloque.nombre}:valor`] = item?.valor ?? '';
    }
    rows.push(allCols.map((c) => row[c] ?? '').join(','));
  }

  await mkdir(outDir, { recursive: true });
  const outputPath = join(outDir, 'documento.csv');
  await writeFile(outputPath, rows.join('\n'), 'utf8');
  return outputPath;
}