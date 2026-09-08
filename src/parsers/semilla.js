/**
 * semilla.js — Parser del lenguaje de programación SEMILLA.
 *
 * SEMILLA es un lenguaje declarativo para describir documentos estructurados.
 * Convierte el texto fuente en un AST (Árbol de Sintaxis Abstracta) que luego
 * alimenta a los exportadores.
 *
 * Sintaxis de referencia (sujeta a evolución):
 *   ---
 *   titulo: Mi Documento
 *   autor: Nombre
 *   ---
 *   campo1: valor1
 *   campo2: valor2, valor3   # valores separados por coma
 *
 * Estructura del AST resultante:
 *   {
 *     meta:    { [clave]: valor },
 *     campos:  { [clave]: string | string[] },
 *     bloques: Array<{ nombre, items }>
 *   }
 */

/**
 * Separa el documento en metadatos, bloques y campos.
 *
 * @param {string} source Código fuente SEMILLA.
 * @returns {object} AST intermedio.
 */
export function parseSemilla(source) {
  const lines = source.split(/\r?\n/);
  const ast = { meta: {}, campos: {}, bloques: [] };

  let section = 'campos'; // 'meta' | 'campos' | bloques
  let currentBlock = null;

  for (const rawLine of lines) {
    const line = rawLine.trim();

    // Saltar líneas vacías y comentarios.
    if (line === '' || line.startsWith('#')) continue;

    // Delimitador de metadatos: bloque entre "---".
    if (line === '---') {
      section = section === 'meta' ? 'campos' : 'meta';
      currentBlock = null;
      continue;
    }

    // Inicio de bloque con "## Nombre".
    const blockMatch = line.match(/^##\s+(.+)$/);
    if (blockMatch) {
      section = 'bloques';
      currentBlock = { nombre: blockMatch[1].trim(), items: [] };
      ast.bloques.push(currentBlock);
      continue;
    }

    // Línea de contenido según la sección actual.
    const match = line.match(/^([^:]+):\s*(.*)$/);
    if (!match) continue;

    const clave = match[1].trim();
    const valor = match[2].trim();

    if (section === 'meta') {
      ast.meta[clave] = valor;
    } else if (section === 'campos') {
      ast.campos[clave] = valor.includes(',') ? valor.split(',').map((v) => v.trim()) : valor;
    } else if (section === 'bloques' && currentBlock) {
      currentBlock.items.push({ clave, valor });
    }
  }

  return ast;
}