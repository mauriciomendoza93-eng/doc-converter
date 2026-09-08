/**
 * task.js — Integración con el sistema de tareas on-demand de Canvas.
 *
 * Permite registrar la conversión de un documento como una tarea Canvas para
 * su seguimiento o ejecución diferida.
 *
 * Nota: La integración real con la API de Canvas queda pendiente de definir.
 * Aquí se define el contrato (shape de entrada/salida) y un stub de
 * implementación que los tests pueden sustituir.
 */

/**
 * Crea una tarea on-demand en Canvas para una conversión realizada.
 *
 * @param {object} input Datos de la tarea.
 * @param {string} input.source Documento fuente (ruta).
 * @param {string} input.outputPath Ruta del archivo resultado.
 * @returns {Promise<string>} ID de la tarea Canvas creada.
 */
export async function createCanvasTask({ source, outputPath }) {
  // Stub: reemplazar por llamada real a la API de Canvas.
  const taskId = `canvas-${Date.now()}`;
  return taskId;
}