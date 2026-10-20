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

# La clasificación por IA es opcional: si Gemini no responde pronto, se sigue sin ella.
_AI_TIMEOUT_MS = 20_000

# Categorías "sin match" que quedan como candidatas para clasificación por IA.
_CATEGORIAS_FALLBACK = {'Otros', 'Otros ingresos'}


def _to_number(value) -> float:
    """Convierte un monto de planilla/extracto a float. Acepta '1,000.00',
    '1.000,00', '(250.50)', '250.50-', 'Bs -1 000,5'. Retorna NaN si no es un número."""
    nan = float('nan')
    if value is None:
        return nan
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    s = str(value).strip()
    if not s:
        return nan
    negative = '-' in s or (s.startswith('(') and s.endswith(')'))
    core = re.sub(r'[^\d,.]', '', s)
    if not re.search(r'\d', core):
        return nan
    if ',' in core and '.' in core:
        decimal = ',' if core.rfind(',') > core.rfind('.') else '.'
        thousands = '.' if decimal == ',' else ','
        core = core.replace(thousands, '').replace(decimal, '.')
    elif ',' in core:
        core = core.replace(',', '') if re.fullmatch(r'\d{1,3}(,\d{3})+', core) else core.replace(',', '.')
    elif core.count('.') > 1:
        if not re.fullmatch(r'\d{1,3}(\.\d{3})+', core):
            return nan
        core = core.replace('.', '')
    try:
        number = float(core)
    except ValueError:
        return nan
    return -abs(number) if negative else number


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


def _kw(text: str, keywords: list) -> bool:
    """¿Aparece alguna palabra clave? Las de hasta 3 letras ('it', 'iva', 'atm',
    'qr'...) exigen límites de palabra: como subcadena, 'it' marcaba como
    Impuestos cualquier concepto con 'hospital' o 'crédito'."""
    for k in keywords:
        if len(k) <= 3:
            if re.search(rf'(?<!\w){re.escape(k)}(?!\w)', text):
                return True
        elif k in text:
            return True
    return False


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
        elif _kw(d, ['interesganado', 'interes ganado', 'rendimiento']):
            categoria = 'Rendimientos'

    if es_egreso:
        if _kw(d, ['rciva', 'impuestos', 'retencion', 'retención', 'it', 'iva', 'determinacion']):
            categoria = 'Impuestos'
        elif _kw(d, ['atm']) or 'retiro' in d or tipo_movimiento == 'Retiro':
            categoria = 'Retiros ATM'
        elif _kw(d, ['apple.com', 'spotify', 'netflix', 'amazon prime', 'hbo', 'disney', 'youtube premium', 'icloud', 'apple storage']):
            categoria = 'Suscripciones'
        elif _kw(d, ['pago de servicios', 'cotes', 'entel', 'cessa', 'delapaz', 'telefonica', 'tigo', 'viva', 'electro', 'agua potable', 'teléfono', 'luz', 'electricidad domicilio', 'gas domiciliario']):
            categoria = 'Servicios Básicos'
        elif _kw(d, ['farmacorp', 'farmacia', 'hospital', 'clinica', 'medico', 'medicina', 'ecografia', 'ecograf', 'dental', 'oftalmolog', 'laboratorio', 'analisis', 'consultorio']):
            categoria = 'Salud'
        elif _kw(d, ['porton', 'soporte magnetico', 'soporte magnético', 'honorario', 'honorarios', 'reparacion', 'reparación', 'mantenimiento', 'plomeria', 'plomería', 'albañil']):
            categoria = 'Mantenimiento/Otros'
        elif _kw(d, ['mancuernas', 'gym', 'smartfit', 'fitness', 'deporte', 'crossfit', 'yoga', 'pilates']):
            categoria = 'Deporte'
        elif _kw(d, ['hamburguesa', 'restaurante', 'cafe', 'café', 'almuerzo', 'comida', 'comedor', 'pizzeria', 'pizzería', 'sushi', 'comida rapida', 'delivery', 'rappi', 'pedidosya']):
            categoria = 'Alimentación'
        elif _kw(d, ['combustible', 'gasolina', 'estacion', 'estación', 'yacuiba', 'yprensa', 'bp', 'shell', 'petrobras']):
            categoria = 'Combustible'
        elif _kw(d, ['supermercado', 'ketal', 'hipermaxi', 'walmart', 'todo en uno', 'hiper']):
            categoria = 'Supermercado'
        elif _kw(d, ['cine', 'teatro', 'concierto', 'evento', 'streaming']):
            categoria = 'Entretenimiento'
        elif _kw(d, ['prestamo', 'préstamo', 'credito', 'crédito', 'cuota', 'amortizacion', 'amortización', 'financiamiento']):
            categoria = 'Préstamos'
        elif 'plazo fijo' in d or 'depósito a plazo' in d or 'deposito a plazo' in d:
            categoria = 'Depósito a Plazo Fijo'
        elif 'interbancaria' in d or _kw(d, ['ach']) or 'transferencia bm' in d:
            categoria = 'Transferencias Interbancarias'
        elif 'yape' in d:
            categoria = 'Pagos vía Yape'
        elif _kw(d, ['qr']) and 'retiro' not in d and 'atm' not in d:
            categoria = 'Transferencia QR'
        elif 'abono en cuenta' in d or 'proveedor' in d or 'pago qr' in d:
            categoria = 'Transferencia a Proveedores'
        elif _kw(d, ['tarjeta', 'dismac', 'visa', 'mastercard']):
            categoria = 'Pagos Tarjetas de Crédito'

    return categoria


def _ai_categorize_fallback(conceptos: list) -> dict:
    """
    Clasifica por IA los conceptos que las reglas de keyword no lograron
    identificar (quedaron en 'Otros' / 'Otros ingresos').

    En el CLI (OMNIROUTE_API_KEY presente) usa la cadena de modelos gratuitos de
    llm_router; en la web, Gemini directo. Best-effort: sin claves o si la llamada
    falla retorna {} y las filas conservan la categoría por defecto de las reglas.
    """
    if not conceptos or not (os.getenv('GEMINI_API_KEY') or os.getenv('OMNIROUTE_API_KEY')):
        return {}

    categorias = ', '.join(c for c in CATEGORIAS_VALIDAS if c not in _CATEGORIAS_FALLBACK)
    lista = '\n'.join(f'- {c}' for c in conceptos)
    prompt = (
        'Eres un clasificador de movimientos bancarios bolivianos. '
        f'Para cada concepto de la lista, asigna EXACTAMENTE una categoría de esta lista: {categorias}. '
        'Si genuinamente ninguna categoría aplica, usa "Otros" (egresos) u "Otros ingresos" (ingresos). '
        'Responde SOLO con un objeto JSON plano {"concepto exacto": "categoria"}, sin markdown ni explicaciones.\n\n'
        f'Conceptos:\n{lista}'
    )
    try:
        if os.getenv('OMNIROUTE_API_KEY'):
            from backend.engines.llm_router import get_router
            text = get_router().complete(prompt, json_mode=True, timeout=60, validate=_valid_mapping).text
        else:
            from google import genai
            from google.genai import types
            client = genai.Client(http_options=types.HttpOptions(
                timeout=_AI_TIMEOUT_MS, retry_options=types.HttpRetryOptions(attempts=1)))
            text = client.models.generate_content(model='gemini-3.6-flash', contents=[prompt]).text.strip()
        text = re.sub(r'^```(json)?|```$', '', text.strip(), flags=re.MULTILINE).strip()
        mapping = json.loads(text)
        return {k: v for k, v in mapping.items() if v in CATEGORIAS_VALIDAS}
    except Exception:
        return {}


def _valid_mapping(text: str):
    try:
        return True if isinstance(json.loads(text), dict) else 'no es un objeto JSON'
    except json.JSONDecodeError:
        return 'JSON inválido'


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


def _pick_amount(df: pd.DataFrame) -> tuple:
    """Elige la columna (o par débito/crédito) del monto. Retorna (serie con signo, advertencias).

    Prioridad: columna única de monto/importe → par débito+crédito (crédito − débito)
    → cualquier débito/crédito suelto → saldo. Un extracto con débito y crédito
    en columnas separadas perdía antes todos los ingresos."""
    cols = list(df.columns)
    has = lambda c, ks: any(k in c.lower() for k in ks)
    single = [c for c in cols if has(c, ('monto', 'importe', 'amount'))]
    debit = [c for c in cols if has(c, ('debito', 'débito', 'cargo', 'retiro', 'debit'))]
    credit = [c for c in cols if has(c, ('credito', 'crédito', 'abono', 'deposito', 'depósito', 'credit'))]
    balance = [c for c in cols if has(c, ('saldo', 'balance'))]
    if single:
        return df[single[0]].map(_to_number), []
    if debit and credit:
        d = df[debit[0]].map(_to_number).abs()
        c = df[credit[0]].map(_to_number).abs()
        both_nan = d.isna() & c.isna()
        amount = c.fillna(0) - d.fillna(0)
        return amount.mask(both_nan), []
    if debit or credit:
        col = (debit or credit)[0]
        sign = -1 if debit else 1
        return df[col].map(_to_number).abs() * sign, [f'solo se encontró la columna "{col}"; los movimientos del otro sentido no se incluyen']
    if balance:
        return df[balance[0]].map(_to_number), [f'se usó la columna "{balance[0]}" como monto (no hay monto/débito/crédito)']
    if len(cols) >= 3:
        return df[cols[2]].map(_to_number), [f'se asumió que "{cols[2]}" es el monto']
    raise ValueError('No se pudo detectar la columna de monto')


def _extract_transactions(df: pd.DataFrame, ai_categorize: bool = True) -> tuple:
    """Retorna (DataFrame de movimientos, lista de advertencias)."""
    date_cols = [c for c in df.columns if any(k in c.lower() for k in ['fecha', 'date', 'fec'])]
    desc_cols = [c for c in df.columns if any(k in c.lower() for k in ['descrip', 'concepto', 'detalle', 'detail', 'memo', 'concept'])]

    if not date_cols and len(df.columns) >= 1:
        date_cols = [df.columns[0]]
    if not desc_cols and len(df.columns) >= 2:
        desc_cols = [df.columns[1]]
    if not (date_cols and desc_cols):
        raise ValueError('No se pudieron detectar columnas de fecha/concepto/monto')

    date_col, desc_col = date_cols[0], desc_cols[0]
    amount, warnings = _pick_amount(df)
    df = df.copy()
    df['__monto__'] = amount
    amount_col = '__monto__'

    # Una fila sin monto numérico es normalmente cabecera/pie del banco y se
    # descarta. Pero si tiene una fecha válida es un movimiento que no pudimos
    # leer: se avisa en vez de perderlo en silencio.
    sin_monto = df[df[amount_col].isna()]
    sospechosas = sin_monto[sin_monto[date_col].map(_parse_date).astype(str).str.fullmatch(r'\d{4}-\d{2}-\d{2}')]
    if len(sospechosas):
        ejemplos = '; '.join(str(v)[:40] for v in sospechosas[desc_col].head(3))
        warnings.append(f'{len(sospechosas)} fila(s) con fecha pero sin monto legible se descartaron (p. ej.: {ejemplos})')

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
    ai_mapping = _ai_categorize_fallback(pendientes) if ai_categorize else {}
    if ai_mapping:
        mask = out['Categoria'].isin(_CATEGORIAS_FALLBACK)
        out.loc[mask, 'Categoria'] = out.loc[mask, 'Concepto'].map(ai_mapping).fillna(out.loc[mask, 'Categoria'])

    # Normalizar el monto a valor absoluto (abs()).
    out['Monto'] = out['Monto'].abs()

    return out[[
        'Tipo de Movimiento', 'Flujo Financiero', 'Monto',
        'Fecha de Operacion', 'Concepto', 'Categoria'
    ]].copy(), warnings


def _finalize_finance_csv(df: pd.DataFrame, banco: str, original_name: str, ai_categorize: bool = True) -> dict:
    """
    Cola común del pipeline financiero: recibe un DataFrame crudo (con columnas
    de fecha/descripción/monto detectables) y un banco ya resuelto, y produce
    el CSV categorizado final. Compartida por la ruta Excel/CSV y la ruta
    de extracción por visión (PDF/imagen).

    Retorna: { 'buffer': io.BytesIO, 'filename': str, 'entity': str, 'period': str, 'rows': int, 'success': bool, 'error'?: str }
    """
    try:
        txns, warnings = _extract_transactions(df, ai_categorize)
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
        'warnings': warnings,
        'success': True,
    }


def convert_to_finance_csv(file_bytes: bytes, original_name: str, ai_categorize: bool = True) -> dict:
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

    return _finalize_finance_csv(df, banco, original_name, ai_categorize)


def convert_to_finance_csv_from_transactions(transacciones: list, banco: str, original_name: str, ai_categorize: bool = True) -> dict:
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

    return _finalize_finance_csv(df, banco, original_name, ai_categorize)
