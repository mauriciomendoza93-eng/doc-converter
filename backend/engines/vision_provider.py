"""
vision_provider.py — Capa de abstracción Strategy/Factory para proveedores de visión.

Permite intercambiar proveedores OCR (Gemini, Tesseract, etc.) sin tocar
la lógica de conversión. Siguiendo el patrón Strategy + Factory.
"""

import os
import time
from google import genai
from google.genai import types
from abc import ABC, abstractmethod


class VisionProvider(ABC):
    """Interfaz común para todos los proveedores de visión/OCR."""

    @abstractmethod
    def process_document(self, file_bytes: bytes, mime_type: str, prompt: str, response_mime_type: str = None) -> str:
        """
        Procesa un documento (PDF o imagen) y retorna texto (Markdown o JSON crudo).

        Args:
            file_bytes: Bytes crudos del archivo
            mime_type: MIME type del archivo (ej. "application/pdf", "image/jpeg")
            prompt: Prompt de instrucciones para el modelo
            response_mime_type: Si se pasa "application/json", fuerza salida JSON válida

        Returns:
            Texto transcrito (Markdown o JSON, según el prompt/response_mime_type)
        """
        pass


class GeminiProvider(VisionProvider):
    """Proveedor usando Google Gemini (gratuito)."""

    def __init__(self):
        if not os.getenv("GEMINI_API_KEY"):
            raise RuntimeError("GEMINI_API_KEY no configurada en variables de entorno")
        # Sin reintentos del SDK: _generate ya reintenta lo transitorio (503) y un 429
        # (cuota diaria) no mejora esperando; con el backoff del SDK tardaba ~20 s en fallar.
        self.client = genai.Client(http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1)))

    def process_text(self, text: str, prompt: str) -> str:
        """Llamada solo-texto (sin imágenes): reestructurar texto ya extraído.
        Usa el mismo modelo y el mismo manejo de errores que process_document."""
        return self._generate([prompt, text], None)

    def process_document(self, file_bytes: bytes, mime_type: str, prompt: str, response_mime_type: str = None) -> str:
        return self._generate(
            [types.Part.from_bytes(data=file_bytes, mime_type=mime_type), prompt],
            response_mime_type,
        )

    # Gemini 3.6 Flash lee mejor, pero en una prueba devolvió vacía una página escaneada
    # (docs/ocr-benchmark.md); 3 Flash es el respaldo, con su propia cuota diaria.
    MODELS = ('gemini-3.6-flash', 'gemini-3-flash-preview')

    def _generate(self, contents: list, response_mime_type: str = None) -> str:
        config = types.GenerateContentConfig(response_mime_type=response_mime_type) if response_mime_type else None
        quota_hit = False
        for model in self.MODELS:
            try:
                text = self._generate_with(model, contents, config)
            except _QuotaExceeded:
                quota_hit = True
                continue
            if text and text.strip():
                return text
        if quota_hit:
            # Cuota gratuita de Gemini agotada (20 requests/día por modelo en el tier
            # gratuito) — reintentar no ayuda, hay que esperar.
            raise RuntimeError(
                'Se alcanzó el límite diario gratuito de Gemini (20 solicitudes/día). '
                'Intenta de nuevo más tarde o usa otra GEMINI_API_KEY.'
            )
        return ''

    def _generate_with(self, model: str, contents: list, config) -> str:
        # Reintentos acotados solo para errores transitorios de disponibilidad
        # (503 UNAVAILABLE por alta demanda). El backoff es corto a propósito:
        # la función serverless corre con maxDuration limitado (plan gratuito).
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
                return response.text
            except Exception as e:
                msg = str(e)
                if '429' in msg or 'RESOURCE_EXHAUSTED' in msg:
                    raise _QuotaExceeded() from e
                is_transient = '503' in msg or 'UNAVAILABLE' in msg
                if is_transient and attempt < max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise


class _QuotaExceeded(Exception):
    pass


class RouterProvider(VisionProvider):
    """Mismo contrato que GeminiProvider, pero repartido entre los modelos gratuitos de
    llm_router (OmniRoute + Gemini) con respaldo y validación. Solo se usa donde hay
    OMNIROUTE_API_KEY: el CLI/Finder (OmniRoute del Mac) y la web del HP (su OmniRoute
    local, el de Hermes); la web de Vercel sigue con Gemini directo. Los documentos van
    por la cadena de OCR medida (vlm_ocr.CLOUD_CHAIN: Claude primero)."""

    @staticmethod
    def _json_ok(text: str):
        import json
        try:
            json.loads(text)
            return True
        except ValueError:
            return 'JSON inválido'

    def process_text(self, text: str, prompt: str) -> str:
        from backend.engines.llm_router import get_router
        return get_router().complete(text, system=prompt).text

    @staticmethod
    def _not_empty(text: str):
        return True if text.strip() else 'respuesta vacía'

    def process_document(self, file_bytes: bytes, mime_type: str, prompt: str, response_mime_type: str = None) -> str:
        from backend.engines import vlm_ocr
        as_json = response_mime_type == 'application/json'
        reply = vlm_ocr._cloud_router().complete(
            prompt, document=(file_bytes, mime_type), vision=True, json_mode=as_json,
            validate=self._json_ok if as_json else self._not_empty, max_tokens=16000, timeout=180,
        )
        return reply.text


class MockLocalProvider(VisionProvider):
    """Proveedor mock local (placeholder futuro para Tesseract/otros)."""

    def process_document(self, file_bytes: bytes, mime_type: str, prompt: str, response_mime_type: str = None) -> str:
        return (
            "[ADVERTENCIA] MockLocalProvider activado. "
            "Este es un placeholder. Integre Tesseract u otro OCR local aquí."
        )


def get_vision_provider(provider: str = "gemini") -> VisionProvider:
    """
    Factory: retorna una instancia del proveedor solicitado.

    Args:
        provider: "gemini" | "mock" (extensible)

    Returns:
        Instancia de VisionProvider

    Raises:
        ValueError: Si el proveedor no es soportado
    """
    providers = {
        "gemini": RouterProvider if os.getenv("OMNIROUTE_API_KEY") else GeminiProvider,
        "mock": MockLocalProvider,
    }
    provider = provider.lower()
    if provider not in providers:
        raise ValueError(f"Proveedor de visión no soportado: '{provider}'. Disponibles: {list(providers.keys())}")
    return providers[provider]()