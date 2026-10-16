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

## Herramientas PDF (servidor propio)

`/tools` une, divide, reordena, rota, comprime, pone marca de agua, tacha texto
(lo elimina de verdad), aplica OCR (Tesseract) y convierte Office→PDF (LibreOffice).
Antes de operar avisa si el PDF tiene cifrado, formularios, firmas digitales o
fuentes no incrustadas. Solo funciona donde hay `DATA_DIR`: en Vercel la página
indica que no está disponible y `/convert` sigue igual.

> **Uso de bajo riesgo.** Sin firmas electrónicas cualificadas, verificación de
> identidad ni edición tipo Acrobat. No hay cuentas, telemetría ni analítica.

**Datos** (`DATA_DIR`, en el HP `/srv/apps/doc-converter/data`):

```
originals/<sha256>/<nombre>          original inmutable (solo lectura), nunca caduca
outputs/<id>/<base>-<op>-v<n>.pdf    cada operación crea una versión nueva
outputs/<id>/meta.json               hashes de entrada/salida, avisos, caducidad
events.jsonl                         solo si no hay DATABASE_URL
```

Los eventos (operación, fecha, hashes, resultado, descargas y borrados) van a la
tabla `events` de la base `doc_converter` en el Postgres del HP; un trigger
impide modificarlos o borrarlos. Los resultados caducan a los `EXPIRY_DAYS`
(30); los originales solo se borran a petición (`DELETE /api/originals/<sha256>`).
**Exportar**: botón *Exportar todo* o `GET /api/export` (ZIP con resultados,
metadatos y eventos).

**Permisos**: el servicio escucha en `127.0.0.1:8300` del HP y solo se publica
en la tailnet (`tailscale serve`), sin login propio: quien está en la tailnet
puede usarlo. El contenedor corre como uid 1000 sin privilegios.

**Despliegue y respaldo**:

```bash
deploy/hp/deploy.sh   # sube código, crea .env y base, levanta el contenedor y el respaldo
```

→ https://hp-server.taila36576.ts.net:8300/tools. El timer
`doc-converter-backup` (04:40 diario) purga caducados y respalda `data/` y
`.env` en el repo restic del HP con la etiqueta `doc-converter`
(14 diarias, 8 semanales, 6 mensuales). Restaurar:

```bash
ssh hp-server 'sudo restic -r /srv/storage/toshiba/Servidor_Backups_HP/restic --password-file /srv/apps/backups/restic.key restore latest --tag doc-converter --target /tmp/restore-dc'
```

## Desarrollo

```bash
.venv/bin/python backend/main.py    # web en http://localhost:3000
vercel --prod                       # desplegar (solo /convert)
DATA_DIR=./data .venv/bin/python backend/main.py   # con herramientas PDF
```

Instalar: `python3.12 -m venv .venv && .venv/bin/pip install -r requirements-server.txt -r requirements-local.txt`.
Tests: `.venv/bin/python -m pytest tests/`.

Requiere `GEMINI_API_KEY` en `.env` (OCR de la web y modo IA local).

## Estructura

```
backend/
  main.py              # FastAPI + Mangum (Vercel)
  tools_api.py         # /api: herramientas PDF, archivos, eventos, exportación
  store.py             # almacén local y registro de eventos
  cli.py               # conversión de archivos del disco
  engines/             # motores: finanzas, documento, local, IA, visión
  utils/               # nombres y validaciones
cli/finder/            # Acción Rápida de Finder (install.sh + run-convert.sh)
frontend/index.html    # web autónoma (CSS y JS en línea)
frontend/tools.html    # herramientas PDF
deploy/hp/             # despliegue y respaldo en el HP
docs/arquitectura.md
```
