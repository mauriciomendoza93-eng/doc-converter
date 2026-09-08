import { test, describe, assert } from 'node:test';
import { parseSemilla } from '../../src/parsers/semilla.js';

describe('parseSemilla', () => {
  test('parsea metadatos entre ---', () => {
    const source = `---
titulo: Mi Doc
autor: Ana
---
campo1: valor1`;
    const ast = parseSemilla(source);
    assert.deepStrictEqual(ast.meta, { titulo: 'Mi Doc', autor: 'Ana' });
  });

  test('parsea campos simples y con comas', () => {
    const source = `---
titulo: Test
---
simple: un valor
lista: a, b, c`;
    const ast = parseSemilla(source);
    assert.strictEqual(ast.campos.simple, 'un valor');
    assert.deepStrictEqual(ast.campos.lista, ['a', 'b', 'c']);
  });

  test('parsea bloques con ##', () => {
    const source = `---
titulo: Test
---
## Detalles
clave1: val1
clave2: val2`;
    const ast = parseSemilla(source);
    assert.strictEqual(ast.bloques.length, 1);
    assert.strictEqual(ast.bloques[0].nombre, 'Detalles');
    assert.deepStrictEqual(ast.bloques[0].items, [
      { clave: 'clave1', valor: 'val1' },
      { clave: 'clave2', valor: 'val2' },
    ]);
  });

  test('ignora comentarios y líneas vacías', () => {
    const source = `# comentario
---
# otro
titulo: T
---

campo: x`;
    const ast = parseSemilla(source);
    assert.strictEqual(ast.meta.titulo, 'T');
    assert.strictEqual(ast.campos.campo, 'x');
  });
});