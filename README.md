# doc-conventer

Conversor de documentos financieros y legales a **CSV** y **Markdown**, con dos
formas de uso: una web desplegada en Vercel y una Acción Rápida de Finder que
convierte archivos de la Mac con clic derecho.

> ⚠️ Nombre del proyecto (con typo intencional): `doc-conventer` (léase "doc-converter").

## Qué convierte

| Entrada | Salida |
|---|---|
| `.xls`, `.xlsx`, `.csv` | CSV financiero (fecha, concepto, monto, tipo, flujo, categoría) |
| `.pdf`, `.png`, `.jpg` de un extracto bancario | CSV financiero |
| `.pdf`, imágenes, `.docx`, `.pptx`, `.doc`, `.rtf`, `.odt`, `.html` | Markdown |

Las extensiones de Office e imágenes sueltas solo están disponibles en el uso
local; la web acepta `.xls`, `.xlsx`, `.csv`, `.pdf`, `.png` y `.jpg`.

## Uso web

<https://doc-conventer.vercel.app> — subir el archivo y descargar el resultado.

## Uso local (Finder)

Convierte con clic derecho, sin subir nada a internet salvo que uses el modo IA.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install -r requirements-local.txt
cli/finder/install.sh
```

Finder > clic derecho > Servicios > **Convertir con Doc Converter**.

Detalle de motores, tiempos medidos y resolución de problemas en
[cli/finder/README.md](cli/finder/README.md).

## Desarrollo

```bash
.venv/bin/python backend/main.py    # web en http://localhost:3000
vercel --prod                       # desplegar
```

Requiere `GEMINI_API_KEY` en `.env` (OCR de la web y modo IA local).

## Estructura

```
backend/
  main.py              # FastAPI + Mangum (Vercel)
  cli.py               # conversión de archivos del disco
  engines/             # motores: finanzas, documento, local, IA, visión
  utils/               # nombres y validaciones
cli/finder/            # Acción Rápida de Finder (install.sh + run-convert.sh)
frontend/index.html    # web autónoma (CSS y JS en línea)
docs/arquitectura.md
```
