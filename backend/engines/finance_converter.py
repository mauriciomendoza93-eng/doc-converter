"""
finance_converter.py — Motor de conversión a CSV financiero (stateless).

Recibe bytes en memoria, retorna io.BytesIO con CSV y nombre de archivo.
7 columnas exactas: Tipo de Movimiento, Flujo Financiero, Monto, Fecha de Operacion,
Concepto, Categoria, Cuenta / Destino.
"""

import io
import json
import os
import re
from datetime import datetime
import pandas as pd

from backend.utils.validators import validate_csv

# Categorías válidas (misma taxonomía usada en finanzas-personales/src/parser.gs
# para mantener consistencia entre ambos proyectos).
CATEGORIAS_VALIDAS = [
    'Alquiler', 'Rendimientos', 'Otros ingresos',
    'Impuestos', 'Retiros ATM', 'Suscripciones', 'Servicios Básicos', 'Salud',
    'Mantenimiento/Otros', 'Deporte', 'Alimentación', 'Combustible', 'Supermercado',
    'Entretenimiento', 'Préstamos', 'Depósito a Plazo Fijo',
    'Transferencias Interbancarias', 'Pagos vía Yape', 'Transferencia QR',
    'Transferencia a Proveedores', 'Pagos Tarjetas de Crédito', 'Otros',
]

# Categorías "sin match" que quedan como candidatas para clasificación por IA.
_CATEGORIAS_FALLBACK = {'Otros', 'Otros ingresos'}


def _parse_date(value) -> str:
    if pd.isna(value):
        return ''
    s = str(value).strip()
    formats = [
        '%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d.%m.%Y',
        '%Y/%m/%d', '%m/%d/%Y', '%d %b %Y', '%d %B %Y',
        '%b %d %Y', '%B %d %Y', '%Y%m%d',
    ]
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
        except ValueError:
            pass
    try:
        return pd.to_datetime(s).strftime('%Y-%m-%d')
    except Exception:
        return s


def _clean_concepto(value) -> str:
    if pd.isna(value) or str(value).strip() == '':
        return 'Sin concepto'
    s = str(value).strip().replace('\n', ' ').replace('\r', ' ')
    s = re.sub(r'\s+', ' ', s)
    if ',' in s and not (s.startswith('"') and s.endswith('"')):
        s = f'"{s}"'
    return s


def _detect_tipo_movimiento(desc: str, amount: float) -> str:
    desc_lower = desc.lower()
    if amount > 0:
        if any(kw in desc_lower for kw in ['transferencia', 'deposito', 'abono', 'ingreso', 'pago recibido']):
            return 'Ingreso'
        return 'Ingreso'
    else:
        if any(kw in desc_lower for kw in ['retiro', 'extraccion', 'atm', 'cajero']):
            return 'Retiro'
        if any(kw in desc_lower for kw in ['transferencia', 'pago', 'compra', 'tarjeta', 'debito']):
            return 'Pago'
        return 'Egreso'


def _detect_flujo_financiero(amount: float) -> str:
    return 'Entrada' if amount > 0 else 'Salida'


def _detect_categoria(desc: str, monto: float, tipo_movimiento: str) -> str:
    """
    Motor de reglas de categorización automática por keyword matching.

    Portado de la función `determinarMetadatos` en
    finanzas/src/parser.gs (paso 4: reglas exhaustivas), sin la etapa de
    historial de usuario (doc-conventer es stateless, no persiste transacciones
    previas). El orden de las condiciones es intencional: reglas más
    específicas primero para evitar falsos positivos (ej. "yape"/"qr" antes
    de la regla genérica de transferencias).
    """
    d = (desc or '').lower()
    es_ingreso = monto > 0 or tipo_movimiento in ('Ingreso', 'Entrada')
    es_egreso = monto < 0 or tipo_movimiento in ('Pago', 'Egreso', 'Retiro')

    categoria = 'Otros ingresos' if monto > 0 else 'Otros'

    if es_ingreso:
        inquilinos_conocidos = ['adela palacios', 'sifuentes ceron', 'gonzales villarpando', 'martha cristina mena']
        es_alquiler = 'alquiler' in d or 'alkiler' in d or any(inq in d for inq in inquilinos_conocidos)
        if es_alquiler:
            categoria = 'Alquiler'
        elif any(k in d for k in ['interesganado', 'interes ganado', 'rendimiento']):
            categoria = 'Rendimientos'

    if es_egreso:
        if any(k in d for k in ['rciva', 'impuestos', 'retencion', 'retención', 'it', 'iva', 'determinacion']):
            categoria = 'Impuestos'
        elif 'atm' in d or 'retiro' in d or tipo_movimiento == 'Retiro':
            categoria = 'Retiros ATM'
        elif any(k in d for k in ['apple.com', 'spotify', 'netflix', 'amazon prime', 'hbo', 'disney', 'youtube premium', 'icloud', 'apple storage']):
            categoria = 'Suscripciones'
        elif any(k in d for k in ['pago de servicios', 'cotes', 'entel', 'cessa', 'delapaz', 'telefonica', 'tigo', 'viva', 'electro', 'agua potable', 'teléfono', 'luz', 'electricidad domicilio', 'gas domiciliario']):
            categoria = 'Servicios Básicos'
        elif any(k in d for k in ['farmacorp', 'farmacia', 'hospital', 'clinica', 'medico', 'medicina', 'ecografia', 'ecograf', 'dental', 'oftalmolog', 'laboratorio', 'analisis', 'consultorio']):
            categoria = 'Salud'
        elif any(k in d for k in ['porton', 'soporte magnetico', 'soporte magnético', 'honorario', 'honorarios', 'reparacion', 'reparación', 'mantenimiento', 'plomeria', 'plomería', 'albañil']):
            categoria = 'Mantenimiento/Otros'
        elif any(k in d for k in ['mancuernas', 'gym', 'smartfit', 'fitness', 'deporte', 'crossfit', 'yoga', 'pilates']):
            categoria = 'Deporte'
        elif any(k in d for k in ['hamburguesa', 'restaurante', 'cafe', 'café', 'almuerzo', 'comida', 'comedor', 'pizzeria', 'pizzería', 'sushi', 'comida rapida', 'delivery', 'rappi', 'pedidosya']):
            categoria = 'Alimentación'
        elif any(k in d for k in ['combustible', 'gasolina', 'estacion', 'estación', 'yacuiba', 'yprensa', 'bp', 'shell', 'petrobras']):
            categoria = 'Combustible'
        elif any(k in d for k in ['supermercado', 'ketal', 'hipermaxi', 'walmart', 'todo en uno', 'hiper']):
            categoria = 'Supermercado'
        elif any(k in d for k in ['cine', 'teatro', 'concierto', 'evento', 'streaming']):
            categoria = 'Entretenimiento'
        elif any(k in d for k in ['prestamo', 'préstamo', 'credito', 'crédito', 'cuota', 'amortizacion', 'amortización', 'financiamiento']):
            categoria = 'Préstamos'
        elif 'plazo fijo' in d or 'depósito a plazo' in d or 'deposito a plazo' in d:
            categoria = 'Depósito a Plazo Fijo'
        elif 'interbancaria' in d or 'ach' in d or 'transferencia bm' in d:
            categoria = 'Transferencias Interbancarias'
        elif 'yape' in d:
            categoria = 'Pagos vía Yape'
        elif 'qr' in d and 'retiro' not in d and 'atm' not in d:
            categoria = 'Transferencia QR'
        elif 'abono en cuenta' in d or 'proveedor' in d or 'pago qr' in d:
            categoria = 'Transferencia a Proveedores'
        elif any(k in d for k in ['tarjeta', 'dismac', 'visa', 'mastercard']):
            categoria = 'Pagos Tarjetas de Crédito'

    return categoria


def _ai_categorize_fallback(conceptos: list) -> dict:
    """
    Clasifica por IA (Gemini) los conceptos que las reglas de keyword no
    lograron identificar (quedaron en 'Otros' / 'Otros ingresos').

    Best-effort: si GEMINI_API_KEY no está configurada o la llamada falla,
    retorna {} y las filas conservan la categoría por defecto de las reglas.
    """
    if not conceptos or not os.getenv('GEMINI_API_KEY'):
        return {}

    try:
        from google import genai

        client = genai.Client()
        lista = '\n'.join(f'- {c}' for c in conceptos)
        categorias = ', '.join(c for c in CATEGORIAS_VALIDAS if c not in _CATEGORIAS_FALLBACK)
        prompt = (
            'Eres un clasificador de movimientos bancarios bolivianos. '
            f'Para cada concepto de la lista, asigna EXACTAMENTE una categoría de esta lista: {categorias}. '
            'Si genuinamente ninguna categoría aplica, usa "Otros" (egresos) u "Otros ingresos" (ingresos). '
            'Responde SOLO con un objeto JSON plano {"concepto exacto": "categoria"}, sin markdown ni explicaciones.\n\n'
            f'Conceptos:\n{lista}'
        )
        response = client.models.generate_content(model='gemini-3.6-flash', contents=[prompt])
        text = response.text.strip()
        text = re.sub(r'^```(json)?|```$', '', text.strip(), flags=re.MULTILINE).strip()
        mapping = json.loads(text)
        return {k: v for k, v in mapping.items() if v in CATEGORIAS_VALIDAS}
    except Exception:
        return {}


def _detect_banco(header_text: str, filename: str) -> str:
    """
    Detecta el banco boliviano analizando el nombre del archivo y el texto de la cabecera.

    Mapeo estricto para Bolivia:
    - "bcp" o "banco de credito" → "BCP"
    - "bnb" → "BNB"
    - "union" o "unión" → "Banco Unión"
    - "mercantil" → "Mercantil Santa Cruz"
    - Default → "Efectivo"
    """
    text = (header_text + " " + filename).lower()

    # Normalizar tildes para búsqueda
    text = text.replace('ó', 'o').replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ú', 'u')

    if any(kw in text for kw in ['bcp', 'banco de credito']):
        return 'BCP'
    if 'bnb' in text:
        return 'BNB'
    if any(kw in text for kw in ['union', 'unión']):
        return 'Banco Unión'
    if 'mercantil' in text:
        return 'Mercantil Santa Cruz'

    return 'Efectivo'


def _extract_transactions(df: pd.DataFrame) -> pd.DataFrame:
    date_cols = [c for c in df.columns if any(k in c.lower() for k in ['fecha', 'date', 'fec'])]
    desc_cols = [c for c in df.columns if any(k in c.lower() for k in ['descrip', 'concepto', 'detalle', 'detail', 'memo', 'concept'])]
    amount_cols = [c for c in df.columns if any(k in c.lower() for k in ['monto', 'importe', 'amount', 'debito', 'credito', 'cargo', 'abono', 'saldo'])]

    if not date_cols and len(df.columns) >= 1:
        date_cols = [df.columns[0]]
    if not desc_cols and len(df.columns) >= 2:
        desc_cols = [df.columns[1]]
    if not amount_cols and len(df.columns) >= 3:
        amount_cols = [df.columns[2]]

    if not (date_cols and desc_cols and amount_cols):
        raise ValueError('No se pudieron detectar columnas de fecha/concepto/monto')

    date_col, desc_col, amount_col = date_cols[0], desc_cols[0], amount_cols[0]

    # Limpieza ETL del monto: convertir a numérico. Lo que no sea número
    # (cabecera del banco, pie de página) se convierte en NaN.
    df[amount_col] = pd.to_numeric(df[amount_col], errors='coerce')

    # Eliminar todas las filas cuyo monto quedó como NaN: filtra automáticamente
    # la cabecera y el pie de página del banco.
    df = df.dropna(subset=[amount_col]).copy()

    out = pd.DataFrame()
    out['Fecha de Operacion'] = df[date_col].apply(_parse_date)
    out['Concepto'] = df[desc_col].apply(_clean_concepto)
    out['Monto'] = df[amount_col]

    # Tipo, flujo y categoría se detectan con el signo original antes de normalizar a absoluto.
    out['Tipo de Movimiento'] = out.apply(lambda r: _detect_tipo_movimiento(str(r['Concepto']), r['Monto']), axis=1)
    out['Flujo Financiero'] = out['Monto'].apply(_detect_flujo_financiero)
    out['Categoria'] = out.apply(
        lambda r: _detect_categoria(r['Concepto'], r['Monto'], r['Tipo de Movimiento']),
        axis=1,
    )

    # Fallback por IA: clasifica los conceptos que las reglas dejaron en
    # 'Otros' / 'Otros ingresos' (best-effort, no bloquea la conversión si falla).
    pendientes = out.loc[out['Categoria'].isin(_CATEGORIAS_FALLBACK), 'Concepto'].unique().tolist()
    ai_mapping = _ai_categorize_fallback(pendientes)
    if ai_mapping:
        mask = out['Categoria'].isin(_CATEGORIAS_FALLBACK)
        out.loc[mask, 'Categoria'] = out.loc[mask, 'Concepto'].map(ai_mapping).fillna(out.loc[mask, 'Categoria'])

    # Normalizar el monto a valor absoluto (abs()).
    out['Monto'] = out['Monto'].abs()

    return out[[
        'Tipo de Movimiento', 'Flujo Financiero', 'Monto',
        'Fecha de Operacion', 'Concepto', 'Categoria'
    ]]


def _finalize_finance_csv(df: pd.DataFrame, banco: str, original_name: str) -> dict:
    """
    Cola común del pipeline financiero: recibe un DataFrame crudo (con columnas
    de fecha/descripción/monto detectables) y un banco ya resuelto, y produce
    el CSV categorizado final. Compartida por la ruta Excel/CSV y la ruta
    de extracción por visión (PDF/imagen).

    Retorna: { 'buffer': io.BytesIO, 'filename': str, 'entity': str, 'period': str, 'rows': int, 'success': bool, 'error'?: str }
    """
    try:
        txns = _extract_transactions(df)
    except Exception as e:
        return {'success': False, 'error': f'Error extrayendo transacciones: {e}'}

    if txns.empty:
        return {'success': False, 'error': 'No se encontraron transacciones válidas'}

    # Rellenar toda la columna "Cuenta / Destino" con el banco detectado
    txns['Cuenta / Destino'] = banco

    # Nombre dinámico: extracto-{banco}-{periodo}.csv
    # El periodo se extrae de las fechas reales de las transacciones (no la fecha
    # del sistema), para que el nombre refleje el contenido real del extracto.
    fechas = pd.to_datetime(txns['Fecha de Operacion'], errors='coerce').dropna()
    if not fechas.empty:
        inicio, fin = fechas.min(), fechas.max()
        if (inicio.year, inicio.month) == (fin.year, fin.month):
            periodo = inicio.strftime('%m-%Y')
        else:
            periodo = f"{inicio.strftime('%m-%Y')}_a_{fin.strftime('%m-%Y')}"
    else:
        periodo = datetime.now().strftime('%m-%Y')

    # Normalizar nombre del banco para el archivo (minúsculas, sin espacios/tildes)
    banco_slug = banco.lower().replace(' ', '-').replace('ó', 'o').replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ú', 'u')
    fname = f"extracto-{banco_slug}-{periodo}.csv"

    csv_content = txns.to_csv(index=False)

    # Validar
    validation = validate_csv(csv_content)
    if not validation['valid']:
        return {'success': False, 'error': '; '.join(validation['issues'])}

    buffer = io.BytesIO(csv_content.encode('utf-8'))
    buffer.seek(0)

    return {
        'buffer': buffer,
        'filename': fname,
        'entity': banco,
        'period': periodo,
        'rows': len(txns),
        'success': True,
    }


def convert_to_finance_csv(file_bytes: bytes, original_name: str) -> dict:
    """
    Stateless: recibe bytes y nombre original, devuelve BytesIO + filename.
    Lee planillas (.xls/.xlsx/.csv).

    Retorna: { 'buffer': io.BytesIO, 'filename': str, 'entity': str, 'period': str, 'rows': int, 'success': bool, 'error'?: str }
    """
    ext = original_name.rsplit('.', 1)[-1].lower() if '.' in original_name else ''

    try:
        if ext in ('xlsx', 'xlsm', 'xlsb'):
            # Leer SIN cabecera para detectar la fila real de columnas
            df = pd.read_excel(io.BytesIO(file_bytes), engine='openpyxl', header=None)
        elif ext == 'xls':
            df = pd.read_excel(io.BytesIO(file_bytes), engine='xlrd', header=None)
        elif ext == 'csv':
            # CSV: leer con header=None también
            df = pd.read_csv(io.BytesIO(file_bytes), encoding='utf-8', on_bad_lines='skip', header=None)
        else:
            return {'success': False, 'error': f'Formato no soportado para finanzas: .{ext}'}
    except Exception as e:
        return {'success': False, 'error': f'Error leyendo archivo: {e}'}

    # Guardar texto de las primeras 30 filas para detección de banco
    header_text = ""
    for i in range(min(30, len(df))):
        header_text += " ".join(df.iloc[i].astype(str).str.lower().tolist()) + " "

    # Detección dinámica de la cabecera real (primeras 30 filas)
    # Conversión segura a string para evitar error numpy.float64
    header_idx = 0
    for i in range(min(30, len(df))):
        # Convierte la fila entera a string, luego a minúsculas, y la une en un solo texto
        row_text = " ".join(df.iloc[i].astype(str).str.lower().tolist())

        if "fecha" in row_text and any(w in row_text for w in ["monto", "cargo", "abono", "importe", "retiro"]):
            header_idx = i
            break

    # Asigna las nuevas cabeceras y recorta la basura superior
    df.columns = df.iloc[header_idx]
    df = df[header_idx + 1:].reset_index(drop=True)

    # Normaliza los nombres de las columnas a string para evitar fallos en el mapeo posterior
    df.columns = df.columns.astype(str).str.strip()

    # Detectar banco usando cabecera + nombre de archivo
    banco = _detect_banco(header_text, original_name)

    return _finalize_finance_csv(df, banco, original_name)


def convert_to_finance_csv_from_transactions(transacciones: list, banco: str, original_name: str) -> dict:
    """
    Variante del pipeline financiero para transacciones ya extraídas (ej. por
    OCR de Gemini sobre un PDF/imagen), en vez de leídas de una planilla.

    `transacciones`: lista de dicts con claves 'fecha', 'descripcion', 'monto'
    (monto con signo: negativo = egreso). `banco`: nombre de banco ya
    detectado (por el propio OCR); si viene vacío, se intenta por nombre de
    archivo con `_detect_banco`.

    Retorna el mismo formato que `convert_to_finance_csv`.
    """
    if not transacciones:
        return {'success': False, 'error': 'No se encontraron transacciones válidas'}

    df = pd.DataFrame(transacciones).rename(columns={
        'fecha': 'Fecha', 'descripcion': 'Descripcion', 'monto': 'Monto',
    })

    banco = (banco or '').strip() or _detect_banco('', original_name)

    return _finalize_finance_csv(df, banco, original_name)
