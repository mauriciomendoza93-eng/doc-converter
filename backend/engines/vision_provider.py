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
        self.client = genai.Client()

    def process_document(self, file_bytes: bytes, mime_type: str, prompt: str, response_mime_type: str = None) -> str:
        config = types.GenerateContentConfig(response_mime_type=response_mime_type) if response_mime_type else None

        # Reintentos acotados solo para errores transitorios de disponibilidad
        # (503 UNAVAILABLE por alta demanda). El backoff es corto a propósito:
        # la función serverless corre con maxDuration limitado (plan gratuito).
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                response = self.client.models.generate_content(
                    model='gemini-3.6-flash',
                    contents=[
                        types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
                        prompt,
                    ],
                    config=config,
                )
                return response.text
            except Exception as e:
                msg = str(e)
                if '429' in msg or 'RESOURCE_EXHAUSTED' in msg:
                    # Cuota gratuita de Gemini agotada (20 requests/día en el
                    # tier gratuito) — reintentar no ayuda, hay que esperar.
                    raise RuntimeError(
                        'Se alcanzó el límite diario gratuito de Gemini (20 solicitudes/día). '
                        'Intenta de nuevo más tarde o usa otra GEMINI_API_KEY.'
                    ) from e
                is_transient = '503' in msg or 'UNAVAILABLE' in msg
                if is_transient and attempt < max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise


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
        "gemini": GeminiProvider,
        "mock": MockLocalProvider,
    }
    provider = provider.lower()
    if provider not in providers:
        raise ValueError(f"Proveedor de visión no soportado: '{provider}'. Disponibles: {list(providers.keys())}")
    return providers[provider]()