"""
Auditoría de la respuesta generada (LLM-as-a-Judge).
Una sola llamada cubre fidelidad, relevancia, nivel de evidencia y verificación
afirmación por afirmación: antes eran dos llamadas con el contexto duplicado.
"""
import logging
from typing import Dict, Any, Optional, List

from app.core.llm_gateway import get_llm_response
from app.core.llm_json import parse_llm_json
from app.core.evidence import (
    is_abstention,
    LEVEL_A_SUFFICIENT,
    LEVEL_B_PARTIAL,
    LEVEL_C_RELATED_INSUFFICIENT,
    LEVEL_D_NO_EVIDENCE,
)

logger = logging.getLogger(__name__)

# Recortes del contexto enviado al juez: la evidencia útil está al principio del pasaje
MAX_PASSAGES = 8
MAX_PASSAGE_CHARS = 900
MAX_PATHS = 12
MAX_NODES = 12

VALID_LEVELS = (LEVEL_A_SUFFICIENT, LEVEL_B_PARTIAL, LEVEL_C_RELATED_INSUFFICIENT, LEVEL_D_NO_EVIDENCE)

EVALUATOR_SYSTEM_PROMPT = """Eres un auditor de sistemas RAG (LLM-as-a-Judge). Auditas una respuesta frente a la evidencia aportada.

Evalúa a la vez:
1. FIDELIDAD: ¿cada afirmación factual está respaldada por la evidencia? Abstenerse honestamente ("No consta en la documentación") es fidelidad 1.0.
2. RELEVANCIA: ¿responde a lo que se preguntó?
3. AFIRMACIONES: extrae como MUCHO las 4 afirmaciones factuales más importantes (máximo 12 palabras cada una) y marca cuáles están respaldadas, citando el documento o camino de grafo concreto.
4. NIVEL DE EVIDENCIA final:
   - LEVEL_A_SUFFICIENT: la evidencia sostiene la respuesta completa.
   - LEVEL_B_PARTIAL: sostiene parte; falta algún dato solicitado.
   - LEVEL_C_RELATED_INSUFFICIENT: las entidades aparecen, pero el hecho concreto no está documentado.
   - LEVEL_D_NO_EVIDENCE: el tema no aparece en la evidencia.

Una afirmación solo está respaldada si los datos concretos (nombres, cifras, relaciones) aparecen explícitamente en la evidencia.

Responde ÚNICAMENTE con este JSON:
{
  "faithfulness_score": 0.95,
  "relevancy_score": 0.90,
  "uncertainty_level": "LEVEL_A_SUFFICIENT",
  "claims": [{"claim_text": "...", "is_grounded": true, "supporting_doc": "archivo.pdf o null", "supporting_path": "(A) -[:REL]-> (B) o null"}],
  "unsupported_claims": [],
  "critique": "Explicación breve",
  "missing_aspects": "Qué falta, si falta algo"
}"""


def _format_context(hybrid_context: Dict[str, Any]) -> str:
    passages = hybrid_context.get("vector_passages", [])[:MAX_PASSAGES]
    paths = hybrid_context.get("graph_paths", [])[:MAX_PATHS]
    nodes = hybrid_context.get("graph_nodes", [])[:MAX_NODES]

    passages_str = "\n".join(
        f"- [{p.get('metadata', {}).get('filename', 'doc')}]: {(p.get('text') or '')[:MAX_PASSAGE_CHARS]}"
        for p in passages
    )
    paths_str = "\n".join(f"- {p.get('path_str', '')}" for p in paths)
    nodes_str = "\n".join(
        f"- {n.get('properties', {}).get('name', 'Nodo')} ({n.get('properties', {}).get('type', 'Entity')})"
        for n in nodes
    )
    return (
        f"--- Pasajes documentales ---\n{passages_str or 'Ninguno'}\n\n"
        f"--- Caminos del grafo ---\n{paths_str or 'Ninguno'}\n\n"
        f"--- Nodos del grafo ---\n{nodes_str or 'Ninguno'}"
    )


def _empty_result(level: str, critique: str, unsupported: List[str]) -> Dict[str, Any]:
    return {
        "is_faithful": False,
        "faithfulness_score": 0.0,
        "relevancy_score": 0.0,
        "grounding_ratio": 0.0,
        "confidence_score": 0.0,
        "uncertainty_level": level,
        "claims": [],
        "unsupported_claims": unsupported,
        "critique": critique,
        "missing_aspects": "",
    }


async def evaluate_rag_response(
    question: str,
    response: str,
    hybrid_context: Dict[str, Any],
    uncertainty_level: Optional[str] = None
) -> Dict[str, Any]:
    """
    Audita la respuesta y calcula el índice de confianza calibrado.
    """
    if not response or len(response.strip()) < 5:
        return _empty_result(LEVEL_D_NO_EVIDENCE, "La respuesta generada está vacía.", ["Respuesta vacía o insuficiente"])

    eval_input = (
        f"PREGUNTA:\n{question}\n\n"
        f"RESPUESTA A AUDITAR:\n{response}\n\n"
        f"EVIDENCIA DISPONIBLE:\n{_format_context(hybrid_context)}"
    )
    messages = [
        {"role": "system", "content": EVALUATOR_SYSTEM_PROMPT},
        {"role": "user", "content": eval_input},
    ]

    try:
        # La salida del juez domina su latencia: se acota, y si aun así se trunca se recupera
        raw = await get_llm_response(
            messages, temperature=0.0, max_tokens=800, response_format={"type": "json_object"}
        )
        data = parse_llm_json(raw)
    except Exception as e:
        logger.error(f"Fallo en la auditoría de la respuesta: {e}")
        return _empty_result(
            uncertainty_level or LEVEL_D_NO_EVIDENCE,
            f"No se pudo auditar la respuesta por error técnico: {e}",
            [f"Error de auditoría: {e}"],
        )

    f_score = max(0.0, min(1.0, float(data.get("faithfulness_score", 0.0))))
    r_score = max(0.0, min(1.0, float(data.get("relevancy_score", 0.0))))

    claims = [c for c in data.get("claims", []) if isinstance(c, dict)]
    grounded = sum(1 for c in claims if c.get("is_grounded", False))
    grounding_ratio = round(grounded / len(claims), 2) if claims else 0.0

    unsupported = list(data.get("unsupported_claims", []) or [])
    for c in claims:
        if not c.get("is_grounded", True) and c.get("claim_text") and c["claim_text"] not in unsupported:
            unsupported.append(c["claim_text"])

    level = data.get("uncertainty_level")
    if level not in VALID_LEVELS:
        level = uncertainty_level or LEVEL_B_PARTIAL

    # Abstención honesta: no alucina, pero tampoco aporta datos -> confianza sustantiva 0
    is_honest_abstention = level == LEVEL_D_NO_EVIDENCE and is_abstention(response)

    if is_honest_abstention:
        confidence_score, is_faithful = 0.0, True
        f_score = r_score = 1.0
    elif f_score < 0.50 or unsupported:
        # Con afirmaciones no demostradas la confianza colapsa de forma no lineal
        confidence_score = round(f_score * min(r_score, grounding_ratio), 2)
        is_faithful = False
    else:
        confidence_score = round(grounding_ratio * (0.60 * f_score + 0.40 * r_score), 2)
        is_faithful = f_score >= 0.70 and grounding_ratio >= 0.70

    logger.info(
        f"Auditoría | Nivel: {level} | Fidelidad {f_score:.2f} · Relevancia {r_score:.2f} · "
        f"Grounding {grounding_ratio:.2f} -> Confianza {confidence_score:.2f}"
    )

    return {
        "is_faithful": is_faithful,
        "faithfulness_score": f_score,
        "relevancy_score": r_score,
        "grounding_ratio": grounding_ratio,
        "confidence_score": confidence_score,
        "uncertainty_level": level,
        "claims": claims,
        "unsupported_claims": unsupported,
        "critique": data.get("critique", ""),
        "missing_aspects": data.get("missing_aspects", ""),
    }
