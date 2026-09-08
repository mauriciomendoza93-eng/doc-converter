#!/usr/bin/env node
/**
 * doc-conventer — Punto de entrada principal.
 *
 * Convierte documentos escritos en el lenguaje SEMILLA a múltiples formatos
 * (CSV, JSON) e integra con el sistema de tareas on-demand de Canvas.
 *
 * Uso:
 *   node src/index.js <ruta_documento> [--format csv|json] [--out dir]
 */
import { Command } from 'commander';
import { convert } from './convert.js';

const program = new Command();

program
  .name('doc-conventer')
  .description('Conversor de documentos SEMILLA a CSV/JSON con integración Canvas')
  .version('0.1.0')
  .argument('<documento>', 'Ruta al documento SEMILLA a convertir')
  .option('-f, --format <fmt>', 'Formato de salida: csv o json', 'csv')
  .option('-o, --out <dir>', 'Directorio de salida', './out')
  .option('--canvas', 'Registrar tarea on-demand en Canvas')
  .action(async (documento, options) => {
    try {
      const result = await convert(documento, options);
      console.log(`✅ Conversión completa: ${result.outputPath}`);
      if (options.canvas) {
        console.log(`🖼️  Tarea Canvas registrada: ${result.canvasTaskId}`);
      }
    } catch (err) {
      console.error(`❌ Error: ${err.message}`);
      process.exit(1);
    }
  });

program.parse(process.argv);