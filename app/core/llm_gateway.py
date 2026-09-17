import os
import logging
from typing import List, Dict, Any, Optional
import litellm

# Configurar logging para registrar advertencias y fallbacks
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuración dinámica de modelos según credenciales disponibles
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")

if OPENROUTER_KEY:
    PRIMARY_MODEL = os.getenv("PRIMARY_MODEL", "openrouter/openai/gpt-4o-mini")
    FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "openrouter/meta-llama/llama-3.3-70b-instruct")
else:
    PRIMARY_MODEL = os.getenv("PRIMARY_MODEL", "gpt-4o-mini")
    FALLBACK_MODEL = os.getenv("FALLBACK_MODEL", "groq/llama-3.3-70b-versatile")


async def get_llm_response(
    messages: List[Dict[str, str]],
    temperature: float = 0.2,
    max_tokens: Optional[int] = 1000,
    **kwargs: Any
) -> str:
    """
    Envía una petición al LLM utilizando LiteLLM.
    Intenta primero llamar al modelo principal. Si ocurre un error o timeout,
    hace un fallback automático al modelo secundario.

    :param messages: Lista de mensajes en formato de chat [{'role': 'user', 'content': '...'}]
    :param temperature: Temperatura para la generación.
    :param max_tokens: Límite máximo de tokens generados.
    :return: Texto de la respuesta generada por el LLM.
    """
    call_kwargs = {}
    if PRIMARY_MODEL.startswith("openrouter/") and OPENROUTER_KEY:
        call_kwargs["api_key"] = OPENROUTER_KEY

    try:
        logger.info(f"Enviando petición a modelo principal: {PRIMARY_MODEL}")
        response = await litellm.acompletion(
            model=PRIMARY_MODEL,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=15.0,
            **call_kwargs,
            **kwargs
        )
        return response.choices[0].message.content.strip()

    except Exception as e:
        logger.warning(
            f"Fallo en modelo principal ({PRIMARY_MODEL}): {str(e)}. "
            f"Ejecutando fallback automático a {FALLBACK_MODEL}..."
        )
        fallback_kwargs = {}
        if FALLBACK_MODEL.startswith("openrouter/") and OPENROUTER_KEY:
            fallback_kwargs["api_key"] = OPENROUTER_KEY

        try:
            response = await litellm.acompletion(
                model=FALLBACK_MODEL,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=20.0,
                **fallback_kwargs,
                **kwargs
            )
            logger.info(f"Respuesta obtenida con éxito desde modelo fallback ({FALLBACK_MODEL}).")
            return response.choices[0].message.content.strip()
        except Exception as fallback_err:
            logger.error(f"Error crítico: Ambos modelos (principal y fallback) fallaron: {str(fallback_err)}")
            raise RuntimeError(f"Error en pasarela LLM (modelos no disponibles): {str(fallback_err)}") from fallback_err
