# Acción Rápida de Finder

`install.sh` registra un Servicio de clic derecho llamado **Convertir con Doc
Converter**, que ejecuta localmente la misma lógica que `POST /convert` sobre los
archivos o carpetas seleccionados:

- `.xls` / `.xlsx` / `.csv` → CSV financiero
- `.pdf` / `.png` / `.jpg` → CSV si Gemini detecta un extracto bancario; si no, Markdown

El resultado se guarda junto al archivo original y nunca sobrescribe un archivo
existente: si el nombre ya existe, agrega `_1`, `_2`, etc. Si seleccionas una
carpeta, se convierten los archivos soportados del primer nivel y los resultados
van a la carpeta hermana `<carpeta>_convertida`.

Como corre en tu Mac, no tiene el límite de 60 s de Vercel. La cuota diaria de
Gemini es la misma que usa la web.

## Instalación

    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
    cli/finder/install.sh

Requiere `GEMINI_API_KEY` en `.env`, en la raíz del proyecto.

## Uso

Finder > selecciona archivo(s)/carpeta > clic derecho > Servicios (o Acciones
rápidas) > **Convertir con Doc Converter**.

Al terminar aparece una notificación. Si hay errores, Automator muestra una
alerta con el detalle. El registro completo queda en
`~/Library/Logs/doc-converter.log`.

Desde la terminal:

    .venv/bin/python -m backend.cli archivo.pdf --route markdown

Si el servicio no aparece de inmediato, ejecuta `killall Finder`.

## Desinstalar

    rm -rf ~/Library/Services/"Convertir con Doc Converter.workflow"
