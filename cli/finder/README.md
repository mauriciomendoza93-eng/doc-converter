# Acción Rápida de Finder

`install.sh` registra dos Servicios de clic derecho:

| Servicio | Motor | Cuándo usarlo |
|---|---|---|
| **Convertir con Doc Converter** | Local (`--engine local`) | Siempre. Segundos, sin cuota. |
| **Convertir con Doc Converter (IA)** | Gemini (`--engine ai`) | Cuando el local no respetó bien la estructura: formularios densos, tablas complejas. |

## Qué hace cada formato (motor local)

| Entrada | Herramienta | Salida |
|---|---|---|
| `.docx`, `.pptx`, `.html` | markitdown | Markdown |
| `.doc`, `.rtf`, `.odt` | textutil (macOS) → markitdown | Markdown |
| `.pdf` con capa de texto | pymupdf4llm | Markdown |
| `.pdf` escaneado o mixto | pymupdf4llm + OCR de Vision (macOS) por página | Markdown |
| `.png`, `.jpg`, `.tiff`, `.heic` | OCR de Vision | Markdown |
| `.xls`, `.xlsx`, `.csv` | pandas | CSV financiero |
| PDF que parece extracto bancario | Gemini | CSV financiero |

El OCR de Vision es nativo de macOS: ~0,2 s por página, en paralelo y sin cuota.
Gemini solo se usa para extractos bancarios, porque extraer transacciones
necesita un modelo; si falla (sin cuota o sin red), cae a Markdown local.

El resultado se guarda junto al archivo original y nunca sobrescribe: si el
nombre existe, agrega `_1`, `_2`. Si seleccionas una carpeta, se convierten los
archivos soportados del primer nivel y los resultados van a la carpeta hermana
`<carpeta>_convertida`.

## Instalación

    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
    .venv/bin/python -m pip install -r requirements-local.txt
    cli/finder/install.sh

`GEMINI_API_KEY` en `.env` solo hace falta para extractos bancarios y el modo IA.

## Uso

Finder > selecciona archivo(s)/carpeta > clic derecho > Servicios (o Acciones
rápidas) > **Convertir con Doc Converter**.

## Cómo saber si está trabajando

- Al empezar aparece una notificación ("Convirtiendo …") y al terminar otra con
  el total y los segundos que tardó.
- Mientras corre, macOS muestra un engranaje girando en la barra de menús.
- Un segundo clic sobre la misma selección mientras aún corre se ignora, para no
  duplicar el trabajo.
- Si algo falla, Automator muestra una alerta con el detalle.
- El registro completo, con tiempos, está en `~/Library/Logs/doc-converter.log`:

      tail -f ~/Library/Logs/doc-converter.log

Desde la terminal:

    .venv/bin/python -m backend.cli archivo.pdf [--engine local|ai] [--route auto|finance|markdown]

Cada línea del resultado indica el motor usado: `[local]`, `[local-ocr]` o `[gemini]`.

Si los servicios no aparecen de inmediato, ejecuta `killall Finder`.

## Desinstalar

    rm -rf ~/Library/Services/"Convertir con Doc Converter.workflow"
    rm -rf ~/Library/Services/"Convertir con Doc Converter (IA).workflow"
