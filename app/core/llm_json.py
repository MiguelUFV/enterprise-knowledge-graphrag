"""
Lectura tolerante del JSON que devuelven los modelos.

Los LLM envuelven la respuesta en ```json, cuelan comas finales y, cuando se agota
max_tokens, cortan la salida a mitad. Antes cada módulo repetía su propio parcheo;
aquí se centraliza y además se recupera el JSON truncado.
"""
import json
import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if "```json" in text:
        text = text.split("```json", 1)[1].split("```", 1)[0]
    elif text.startswith("```"):
        partes = text.split("\n", 1)
        text = partes[1] if len(partes) > 1 else text
        text = text.rsplit("```", 1)[0]
    return text.strip()


def _pending_closers(text: str) -> Optional[str]:
    """Cierres que faltan para equilibrar el fragmento, o None si está dentro de una cadena."""
    pila = []
    en_cadena = escape = False
    for ch in text:
        if en_cadena:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                en_cadena = False
            continue
        if ch == '"':
            en_cadena = True
        elif ch == "{":
            pila.append("}")
        elif ch == "[":
            pila.append("]")
        elif ch in "}]":
            if pila:
                pila.pop()
    return None if en_cadena else "".join(reversed(pila))


def _repair_truncated(text: str) -> Optional[Any]:
    """
    Recorta la salida cortada hasta el último elemento completo y cierra las estructuras.
    """
    for corte in range(len(text) - 1, 0, -1):
        if text[corte] not in ",}]":
            continue
        fragmento = text[:corte] if text[corte] == "," else text[:corte + 1]
        cierres = _pending_closers(fragmento)
        if cierres is None:
            continue
        try:
            return json.loads(fragmento + cierres)
        except json.JSONDecodeError:
            continue
    return None


def parse_llm_json(raw: str) -> Any:
    """
    Convierte la respuesta del modelo en objeto Python.

    :raises ValueError: si no se puede recuperar ningún JSON válido.
    """
    if not raw or not raw.strip():
        raise ValueError("Respuesta vacía del modelo")

    text = _strip_fences(raw)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    sin_comas = re.sub(r",\s*([\]}])", r"\1", text)
    try:
        return json.loads(sin_comas)
    except json.JSONDecodeError:
        pass

    recuperado = _repair_truncated(sin_comas)
    if recuperado is not None:
        logger.warning("Respuesta JSON truncada del modelo: recuperada parcialmente.")
        return recuperado

    raise ValueError(f"No se pudo interpretar el JSON del modelo: {text[:160]}...")
