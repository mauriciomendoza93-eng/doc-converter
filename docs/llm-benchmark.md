# Modelos gratuitos para el modo IA (medido el 2026-10-20)

Caso: formulario de 2 páginas con dos columnas, tabla de ítems y cláusulas, pasado por el OCR
plano de la Mac (2 015 caracteres; mezcla columnas y lee mal un dígito: `102345602ęŚ`). Se
mide cuánto del texto sobrevive (recall), si las 12 cifras clave llegan intactas, y que el
modelo **no corrija** el dígito dañado. Las llamadas van por OmniRoute (`/v1/chat/completions`,
con `stream: true`) salvo Gemini directo.

| Modelo | Tiempo | Recall | Cifras | Notas |
|---|---|---|---|---|
| gemini-3-flash-preview (directo) | 8.4 s | 1.00 | 12/12 | cuota diaria baja |
| agy/gemini-3.1-flash-lite | 10.0 s | 1.00 | 12/12 | |
| agy/claude-sonnet-4-6 | 11.1 s | 1.00 | 12/12 | el más fiel en texto denso |
| cloudflare llama-3.3-70b-fp8-fast | 11.6 s | 1.00 | 12/12 | en texto denso en cifras conserva 82-87 % |
| cloudflare qwen3-30b-a3b | 13.1 s | 1.00 | 12/12 | en texto denso conserva 56-63 % |
| nvidia gpt-oss-20b | 19.3 s | 1.00 | — | |
| cloudflare gpt-oss-120b | 27.3 s | 1.00 | — | en texto denso a veces resume |
| nvidia nemotron-3-ultra-550b | 48.9 s | 1.00 | — | lento |
| cloudflare llama-4-scout | 24.4 s | 0.79 | — | pierde contenido |
| cloudflare mistral-small-3.1-24b | 24.5 s | 0.77 | — | pierde contenido |

Ninguno "corrigió" el dígito dañado (el prompt lo prohíbe). La lección más útil: **responder no
es acertar**. En un documento de 10 páginas con 360 importes, `llama-3.3`, `qwen` y
`gpt-oss-120b` devolvieron resúmenes o perdieron 15-45 % del texto, y `gemini-3.1-flash-lite`
llegó a repetir importes inventados; por eso toda respuesta pasa por `text_structurer.validator`.

## Hallazgos de infraestructura

- Sin `stream`, el gateway de OmniRoute corta a ~33 s (504) a los modelos lentos; con `stream`
  responden. El router siempre usa streaming.
- Conexiones de **Mistral y Gemini en OmniRoute: "authentication expired"** (401). Hay que
  reconectarlas en el panel de OmniRoute; mientras tanto el router usa Gemini directo con
  `GEMINI_API_KEY`. `omniroute providers test-all` da 403 con la clave de cliente (no es admin)
  y no es fiable: usa `llm_router probe`.
- De 189 modelos de chat candidatos, solo 43 respondían: NVIDIA lista IDs que su catálogo
  vivo no tiene (400/404) y Cloudflare/NVIDIA dan 504 bajo carga. De ahí la memoria de
  modelos caídos (`~/Library/Caches/doc-converter/llm-health.json`).
- OmniRoute cachea respuestas idénticas (0.0 s): repetir una prueba no mide el modelo.
- La cuota de Gemini directo es por modelo y diaria; `gemini-3.6-flash` se agotó y volvió a
  funcionar horas después.

## Visión (`--engine vision`)

Cadena: agy/gemini-3.1-flash-lite → agy/claude-sonnet-4-6 → gemini-3-flash-preview →
gemini-3.6-flash. Los PDF se renderizan a JPEG para los modelos que no aceptan PDF. Probado
solo con el formulario escaneado de arriba: lee bien las cifras que el OCR confundió
(`7,740.00`, NIT `1023456028`), que es su ventaja sobre `local`/`ai`. Los modelos de visión
de NVIDIA/Cloudflare no se incluyeron: `llama-3.2-11b-vision` de NVIDIA respondió pero en
19 s para un "ok", y `llama-4-scout` pierde contenido en texto. Es un punto por medir con más
documentos antes de ampliar la cadena.
