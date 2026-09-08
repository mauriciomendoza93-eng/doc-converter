"""
validators.py — Validadores de salida Markdown y CSV.

Reglas:
- Markdown: headings bien formados, formato GFM básico.
- CSV financiero: exactamente 3 columnas — Fecha, Descripcion, Monto.
"""

import re


def validate_markdown(content: str) -> dict:
    """Verifica que el Markdown tenga headings y estructura mínima."""
    issues = []
    if not re.search(r'^#{1,6}\s+', content, re.MULTILINE):
        issues.append('No se detectaron headings (H1-H6).')
    # Chequeo de tablas GFM básicas: filas con pipes y separadores ---.
    if '|' in content and not re.search(r'\|[\s\-:]+\|', content):
        issues.append('Posible tabla GFM mal formada (sin separadores).')
    return {'valid': len(issues) == 0, 'issues': issues}


def validate_csv(content: str) -> dict:
    """
    Verifica que el CSV tenga exactamente las columnas:
    Tipo de Movimiento, Flujo Financiero, Monto, Fecha de Operacion,
    Concepto, Categoria, Cuenta / Destino.

    La columna 'Categoria' admite valores vacios, nulos o string vacio.
    """
    import csv
    import io

    reader = csv.reader(io.StringIO(content))
    try:
        header = next(reader)
    except StopIteration:
        return {'valid': False, 'issues': ['CSV vacío.']}

    expected = [
        'Tipo de Movimiento',
        'Flujo Financiero',
        'Monto',
        'Fecha de Operacion',
        'Concepto',
        'Categoria',
        'Cuenta / Destino',
    ]
    if header != expected:
        return {
            'valid': False,
            'issues': [f'Cabeceras incorrectas. Esperadas: {expected}, encontradas: {header}'],
        }

    # Validar que 'Categoria' pueda estar vacía (no es un error)
    # Revisar filas para confirmar que hay al menos una transacción
    rows = list(reader)
    if not rows:
        return {'valid': False, 'issues': ['CSV sin filas de datos.']}

    return {'valid': True, 'issues': []}
