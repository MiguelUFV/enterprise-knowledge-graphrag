"""
Servicio de transcripción de voz local usando faster-whisper.
Recibe audio en formato webm/wav y devuelve texto transcrito.
Sin dependencias de API externas — todo se ejecuta localmente.
"""
import os
import asyncio
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Tamaño del modelo configurable: tiny | base | small | medium | large
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "base")

# Modelo cargado en memoria una sola vez (singleton)
_whisper_model = None


def get_whisper_model():
    """
    Carga el modelo Whisper en memoria la primera vez que se llama.
    Las llamadas posteriores reutilizan la instancia cargada.
    """
    global _whisper_model
    if _whisper_model is None:
        try:
            from faster_whisper import WhisperModel
            logger.info(f"Cargando modelo Whisper '{WHISPER_MODEL_SIZE}' en CPU...")
            _whisper_model = WhisperModel(
                WHISPER_MODEL_SIZE,
                device="cpu",
                compute_type="int8"  # int8 es más rápido en CPU sin pérdida notable de calidad
            )
            logger.info(f"Modelo Whisper '{WHISPER_MODEL_SIZE}' cargado con éxito.")
        except Exception as e:
            logger.error(f"Error al cargar el modelo Whisper: {e}")
            raise RuntimeError(f"No se pudo cargar el modelo Whisper: {e}") from e
    return _whisper_model


async def transcribe_audio(audio_bytes: bytes, audio_format: str = "webm") -> dict:
    """
    Transcribe audio recibido como bytes.

    :param audio_bytes: Bytes del archivo de audio (webm, wav, mp3...).
    :param audio_format: Extensión del formato de audio.
    :return: Diccionario con 'text' (transcripción), 'language', y 'duration'.
    """
    if not audio_bytes or len(audio_bytes) < 100:
        return {"text": "", "language": "es", "duration": 0.0, "error": "Audio vacío o demasiado corto"}

    # Guardar en fichero temporal porque faster-whisper necesita una ruta de archivo
    suffix = f".{audio_format}"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    def _run_transcription():
        model = get_whisper_model()
        # language=None deja que Whisper detecte el idioma automáticamente
        segments, info = model.transcribe(
            tmp_path,
            language=None,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500}
        )
        # Los segmentos son un generador perezoso: consumirlo dentro del hilo
        return " ".join(segment.text.strip() for segment in segments).strip(), info

    try:
        # Whisper es CPU-intensivo: ejecutarlo fuera del event loop
        full_text, info = await asyncio.to_thread(_run_transcription)

        logger.info(
            f"Transcripción completada: idioma='{info.language}', "
            f"duración={info.duration:.1f}s, texto='{full_text[:60]}...'"
        )

        return {
            "text": full_text,
            "language": info.language,
            "duration": round(info.duration, 2)
        }

    except Exception as e:
        logger.error(f"Error durante la transcripción: {e}")
        return {"text": "", "language": "es", "duration": 0.0, "error": str(e)}

    finally:
        # Limpiar el fichero temporal siempre
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass
