# OCR de escaneos y fotos: evaluación (2026-10-28)

Problema: con el OCR de Vision, la conversión local de escaneos y fotos salía con caracteres
cambiados ("Ei BANCO", "unilateraimente ios", "desemboiso", "edame", "iforniación"), letras
perdidas en el margen y basura de sellos ("LUNCA DEL RO / NO"). El modo IA arreglaba el
orden, pero partía del mismo texto mal leído y además mandaba el contenido a la nube.

Documento de prueba: testimonio notarial de préstamo de 15 páginas, escaneado en fax
(CCITT, 1 bit, 300 ppp), con sellos y márgenes cortados. Se sumó una foto simulada de una
página (perspectiva, sombra, ruido, desenfoque y JPEG 70 %).

Equipo: MacBook con M4, 16 GB, macOS 27. **Medido con batería y modo de bajo consumo
activo**; enchufado y sin bajo consumo los tiempos deberían bajar a la mitad o un tercio.

## Plataformas consideradas antes de desarrollar

| Opción | Local | Veredicto |
|---|---|---|
| Mistral OCR 4 (API) | No | Muy buena en escaneos y devuelve Markdown, pero es de pago (US$ 2-4 por 1 000 págs.), requiere cuenta y el documento sale de la Mac. Útil como respaldo en la nube; no como motor por defecto. |
| MarkItDown (Microsoft) | Sí | Ya se usa para Office/HTML. Su OCR (`markitdown-ocr`) delega en un modelo de visión: no resuelve el problema por sí mismo. |
| Doc2Markdown, File2Markdown, Google Document AI | No | Web o nube; misma objeción de privacidad, sin integración con Finder ni lotes. |
| docling, MinerU 2.5, Ollama | Sí | Descartados antes (ver `CLAUDE.md`): peor OCR, muy lentos o recortaban contenido. |
| **Modelos OCR pequeños vía MLX** | Sí | GLM-OCR, PaddleOCR-VL-1.6 y LightOnOCR-3 encabezan OmniDocBench v1.6 y corren en Apple Silicon. Evaluados abajo. |

Ninguna plataforma existente ofrece a la vez OCR de calidad, ejecución local y el flujo que
ya tiene el proyecto (Finder, lotes, informe, extractos bancarios). Por eso los lectores se
integraron en el motor existente en vez de reemplazarlo.

## Modelos locales (página 5, texto denso)

| Modelo (mlx-community) | Tiempo/pág. | Coincidencia con Vision | Resultado |
|---|---|---|---|
| Vision (actual) | 0,2 s | — | "edame", "arciles,jen", "státo)mondicionado", "iforniación" |
| **GLM-OCR 8 bits** | 42-113 s | 0,87 | Casi limpio: recupera "exclusiva", "mediante", "parciales"; 2-3 errores menores |
| GLM-OCR 4 bits | ~30 % menos | 0,87 | Más errores que 8 bits ("recibita", "descriitos", "desembolos") |
| PaddleOCR-VL-1.6 4 bits | 15-31 s | 0,47 | Peor que Vision en este escaneo ("xcluciva", "y�/acidicionado") |
| LightOnOCR-3 1B 8 bits | 39-200 s | 0,91 | Lee bien, pero mete cajas `![text](x,y…)` y entró en bucle (18 000 caracteres) |

Reducir la imagen a 1 280 px hace que GLM-OCR entre en bucle (6 000 tokens): se usa un lado
máximo de 2 000 px (~150 ppp en A4/carta).

## Documento completo (GLM-OCR 8 bits + validación por página)

| | Vision | GLM-OCR |
|---|---|---|
| Palabras no reconocidas por el corrector de macOS (es) | **10,8 %** (547 de 5 049) | **3,1 %** (154 de 5 028; incluye códigos y URLs) |
| Tiempo, 15 págs. | 21 s | 1 217 s (bajo consumo y batería) |
| Páginas en que se descartó el modelo | — | 1 (formulario: omitió campos, coincidencia 54 %) → Vision |
| Foto simulada de una página | — | 92 s; 4,5 % de palabras no reconocidas, por letras ausentes en el margen |

Fallos del modelo encontrados y cubiertos:

- **Omitir bloques** (formulario de la pág. 1): la coincidencia con Vision bajó a 54 % y se usó Vision.
- **Comentar en vez de transcribir** en una página casi en blanco ("The text is too blurry
  to be accurately transcribed."): se eliminan esas frases y, si Vision no vio texto, solo se
  acepta una lectura de 20 palabras o más.
- **Bucles**: tope de 5 000 tokens y detección de fragmentos repetidos.
- **Encabezados y pies repetidos** (código de seguridad, URL de verificación): el modelo
  suele omitirlos. Las cifras que solo leyó Vision se listan en el aviso para revisarlas.

## Nube frente a local (actualización del mismo día)

Mismas páginas (1 formulario, 5 y 10 densas, foto simulada) y mismo criterio. "No reconocidas"
es la fracción de palabras que el corrector de macOS (es) no reconoce; la cobertura es la
coincidencia con el OCR plano de Vision.

| Lector | No reconocidas | Tiempo/pág. | Observaciones |
|---|---|---|---|
| Vision | 10,5 % | 0,2 s | Ilegible en tramos |
| GLM-OCR local | 3,3 % | 15-90 s | Escribió "recibita" donde dice "ochenta y cuatro (84) meses" y omitió una frase entera; omite encabezado y pie |
| Gemini 3 Flash | 3,8 % | 13-28 s | Peor en la foto (42 errores en 591 palabras) |
| Gemini 3.1 Flash-Lite (OmniRoute) | 4,1 % | 10-25 s | |
| Gemini 3.6 Flash | 1,8 % | 8-30 s | Devolvió **vacía** la pág. 10 |
| **Claude Sonnet 4.6 (OmniRoute, gratis)** | **1,5 %** | 10-64 s | Lo más fiel al verificar contra la imagen: "ochenta y cuatro (84)", "abstenerse", "mandato recíproco"; conserva código de seguridad y URL |

La pág. 1 (formulario) parecía mal leída por todos (cobertura 0,53) porque docvision repetía el
formulario como tabla y como lista; desde entonces se valida contra el OCR plano.

**Familia Claude 5 / 5.5.** OmniRoute lista Sonnet 5, Opus 5, Fable 5 y Haiku 4.5 (proveedores
dva, kilocode, aug, ddgw), pero todos respondieron 503 (conexiones caídas o sin suscripción) y no
expone la familia 5.5. No hay `ANTHROPIC_API_KEY`. Costo estimado por 1 000 páginas (1 600 tokens
de entrada + 1 800 de salida, precios oficiales): Haiku 5.5 ≈ US$ 1, Sonnet 5.5 ≈ 21, Sonnet 4.6 ≈ 32,
Opus 5.5 ≈ 42; Mistral OCR 2-4. Haiku 5.5 queda primero en la cadena en cuanto haya clave, sin
medir: cada página igual se valida contra Vision y, si falla, sigue la cadena.

## Decisión

- Escaneos y fotos (`DOC_CONVERTER_OCR=auto`, por defecto): nube primero (Haiku 5.5 con clave →
  Claude Sonnet 4.6 → Gemini 3 Flash → Flash-Lite → Gemini 3.6), luego GLM-OCR local y al final
  Vision. Cada página se valida contra el OCR plano de Vision. `--ocr local` no deja salir la
  imagen de la Mac.
- second-brain usa `local`: su regla es que ningún documento sale a un LLM en la ingesta.
- Web (Vercel y HP): Gemini directo, con Gemini 3 Flash de respaldo si 3.6 devuelve vacío o sin cuota.
- Un solo servicio de Finder. El modo IA sigue en la terminal (`--engine ai`, `--engine vision`).
- Mistral OCR no hace falta: Claude por OmniRoute lee mejor gratis y Haiku 5.5 cuesta menos.
