"""
finance_converter.py — Motor de conversión a CSV financiero (stateless).

Recibe bytes en memoria, retorna io.BytesIO con CSV y nombre de archivo.
7 columnas exactas: Tipo de Movimiento, Flujo Financiero, Monto, Fecha de Operacion,
Concepto, Categoria, Cuenta / Destino.
"""

import io
import re
from datetime import datetime
import pandas as pd

from backend.utils.validators import validate_csv


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

    # Tipo y flujo se detectan con el signo original antes de normalizar a absoluto.
    out['Tipo de Movimiento'] = out.apply(lambda r: _detect_tipo_movimiento(str(r['Concepto']), r['Monto']), axis=1)
    out['Flujo Financiero'] = out['Monto'].apply(_detect_flujo_financiero)

    # Normalizar el monto a valor absoluto (abs()).
    out['Monto'] = out['Monto'].abs()
    out['Categoria'] = ''

    return out[[
        'Tipo de Movimiento', 'Flujo Financiero', 'Monto',
        'Fecha de Operacion', 'Concepto', 'Categoria'
    ]]


def convert_to_finance_csv(file_bytes: bytes, original_name: str) -> dict:
    """
    Stateless: recibe bytes y nombre original, devuelve BytesIO + filename.

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

    try:
        txns = _extract_transactions(df)
    except Exception as e:
        return {'success': False, 'error': f'Error extrayendo transacciones: {e}'}

    if txns.empty:
        return {'success': False, 'error': 'No se encontraron transacciones válidas'}

    # Rellenar toda la columna "Cuenta / Destino" con el banco detectado
    txns['Cuenta / Destino'] = banco

    # Nombre dinámico: extracto-{banco}-{mes}-{año}.csv
    now = datetime.now()
    mes = now.strftime('%m')
    año = now.strftime('%Y')
    # Normalizar nombre del banco para el archivo (minúsculas, sin espacios/tildes)
    banco_slug = banco.lower().replace(' ', '-').replace('ó', 'o').replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ú', 'u')
    fname = f"extracto-{banco_slug}-{mes}-{año}.csv"

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
        'period': f'{mes}-{año}',
        'rows': len(txns),
        'success': True,
    }