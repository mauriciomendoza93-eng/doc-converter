"""
vision_provider.py — Capa de abstracción Strategy/Factory para proveedores de visión.

Permite intercambiar proveedores OCR (Gemini, Tesseract, etc.) sin tocar
la lógica de conversión. Siguiendo el patrón Strategy + Factory.
"""

import os
from google import genai
from google.genai import types
from abc import ABC, abstractmethod


class VisionProvider(ABC):
    """Interfaz común para todos los proveedores de visión/OCR."""

    @abstractmethod
    def process_document(self, file_bytes: bytes, mime_type: str, prompt: str) -> str:
        """
        Procesa un documento (PDF o imagen) y retorna texto Markdown.

        Args:
            file_bytes: Bytes crudos del archivo
            mime_type: MIME type del archivo (ej. "application/pdf", "image/jpeg")
            prompt: Prompt de instrucciones para el modelo

        Returns:
            Texto Markdown transcrito
        """
        pass


class GeminiProvider(VisionProvider):
    """Proveedor usando Google Gemini (gratuito)."""

    def __init__(self):
        if not os.getenv("GEMINI_API_KEY"):
            raise RuntimeError("GEMINI_API_KEY no configurada en variables de entorno")
        self.client = genai.Client()

    def process_document(self, file_bytes: bytes, mime_type: str, prompt: str) -> str:
        response = self.client.models.generate_content(
            model='gemini-3.6-flash',
            contents=[
                types.Part.from_bytes(data=file_bytes, mime_type=mime_type),
                prompt,
            ]
        )
        return response.text


class MockLocalProvider(VisionProvider):
    """Proveedor mock local (placeholder futuro para Tesseract/otros)."""

    def process_document(self, file_bytes: bytes, mime_type: str, prompt: str) -> str:
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