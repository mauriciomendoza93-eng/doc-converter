#!/usr/bin/env python3
"""
cli.py — Conversión local de archivos y carpetas (misma lógica que POST /convert).

Uso:
    python -m backend.cli <archivo|carpeta> [...] [--route auto|finance|markdown]
                          [--engine local|ai|vision] [--ocr auto|local|vision] [--jobs N] [--timeout S]
                          [--no-recursive] [--force] [--dry-run]

--engine local (default): Office con markitdown y PDF con capa de texto con pymupdf4llm,
en la Mac. Las páginas escaneadas y las imágenes las lee un modelo de visión según --ocr:
auto (default) prueba la nube (Claude/Gemini, gratis vía OmniRoute y GEMINI_API_KEY), luego
GLM-OCR en la Mac y al final el OCR de Vision; local no deja salir la imagen de la Mac
(GLM-OCR → Vision); vision usa solo Vision. Cada lectura se valida contra Vision
(backend/engines/vlm_ocr.py). Gemini también interviene en extractos bancarios. --engine ai:
todo PDF/imagen pasa por Gemini, pero solo el TEXTO que ya extrajo la Mac, para que
lo reestructure (una solicitud por documento). --engine vision: manda las imágenes
del documento a Gemini, como la web; el más lento y el que más cuota gasta, pero el
único que puede corregir lo que el OCR leyó mal.

El resultado (.md o .csv) se escribe junto al archivo original y nunca sobrescribe:
si el nombre existe se agrega un sufijo numérico. Las carpetas se procesan con
todas sus subcarpetas (--no-recursive: solo el primer nivel) y sus resultados van a
una carpeta hermana `<carpeta>_convertida` con la misma estructura, más un
`_informe.json` con el detalle. Volver a ejecutar sobre la misma carpeta solo
reconvierte lo que cambió (--force: todo).

Los archivos se convierten en paralelo, cada uno en su propio proceso con tiempo
límite: uno corrupto o colgado no detiene el lote.
"""

import argparse
import os
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))

from backend import batch  # noqa: E402  (después de ajustar sys.path)

DEFAULT_JOBS = max(1, min(3, (os.cpu_count() or 2) - 1))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='doc-convert', description='Convierte documentos a CSV/Markdown.')
    parser.add_argument('paths', nargs='+', help='Archivos o carpetas a convertir')
    parser.add_argument('--route', choices=('auto', 'finance', 'markdown'), default='auto')
    parser.add_argument('--engine', choices=('local', 'ai', 'vision'), default='local')
    parser.add_argument('--ocr', choices=('auto', 'local', 'vision'),
                        help='lector de escaneos y fotos (default: DOC_CONVERTER_OCR o auto)')
    parser.add_argument('--jobs', type=int, default=DEFAULT_JOBS, help='archivos en paralelo (default %(default)s)')
    parser.add_argument('--timeout', type=int, default=batch.DEFAULT_TIMEOUT, help='segundos máximos por archivo (default %(default)s)')
    parser.add_argument('--no-recursive', action='store_true', help='en carpetas, solo el primer nivel')
    parser.add_argument('--force', action='store_true', help='reconvertir aunque la carpeta ya esté convertida y sin cambios')
    parser.add_argument('--dry-run', action='store_true', help='listar qué se convertiría, sin convertir')
    args = parser.parse_args(argv)

    if args.ocr:
        os.environ['DOC_CONVERTER_OCR'] = args.ocr  # lo heredan los workers
    plan = batch.plan(args.paths, recursive=not args.no_recursive)
    for m in plan.missing:
        print(f'Error: no existe {m}', file=sys.stderr)
    if not plan.items:
        print('No se encontraron archivos soportados.', file=sys.stderr)
        if plan.ignored:
            print('Ignorados: ' + _ignored_text(plan.ignored), file=sys.stderr)
        return 1

    if args.dry_run:
        for it in plan.items:
            print(f'{it.rel}  ->  {it.out_dir}')
        print(f'{len(plan.items)} archivo(s) a convertir.')
        return 0

    stats = batch.run(plan, args.engine, args.route, jobs=args.jobs, timeout=args.timeout, force=args.force)

    parts = [f'{stats["converted"]} convertido(s)']
    if stats['unchanged']:
        parts.append(f'{stats["unchanged"]} sin cambios')
    if stats['skipped']:
        parts.append(f'{stats["skipped"]} omitido(s)')
    if stats['errors']:
        parts.append(f'{stats["errors"]} con error')
    if plan.ignored:
        parts.append('ignorados: ' + _ignored_text(plan.ignored))
    print(f'Resumen: {", ".join(parts)} en {stats["seconds"]}s')
    for root in plan.out_roots:
        if (root / batch.REPORT_NAME).exists():
            print(f'Informe: {root / batch.REPORT_NAME}')
    return 1 if stats['errors'] or plan.missing else 0


def _ignored_text(ignored) -> str:
    return ', '.join(f'{k}×{v}' for k, v in ignored.most_common(6))


if __name__ == '__main__':
    sys.exit(main())
