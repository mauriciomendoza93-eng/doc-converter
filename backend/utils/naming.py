"""
naming.py — Generación dinámica de nombres de archivo de salida.
"""

import re
from datetime import datetime


def generate_markdown_filename(original_name: str) -> str:
    """Convierte el nombre original a slug markdown: minúsculas, espacios→guiones."""
    base = original_name.rsplit('.', 1)[0]
    slug = re.sub(r'[^a-zA-Z0-9À-ɏ]+', '-', base).strip('-').lower()
    return f"{slug}.md"


def generate_csv_filename(entity: str, period: str) -> str:
    """
    Genera nombre CSV para extractos financieros.
    entity: entidad bancaria (slug)
    period: periodo en formato 'YYYY-MM' o 'mes-año' (ej. 'agosto-2026')
    """
    ent_slug = re.sub(r'[^a-zA-Z0-9]+', '-', entity).strip('-').lower()
    per_slug = re.sub(r'[^a-zA-Z0-9]+', '-', period).strip('-').lower()
    return f"extracto-{ent_slug}-{per_slug}.csv"
