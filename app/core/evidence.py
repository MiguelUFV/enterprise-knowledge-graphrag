"""
Clasificación epistémica de la evidencia recuperada.

Es determinista (0 ms, sin LLM): se apoya en las señales que ya produjo la recuperación
(cross-encoder, BM25 y vector). El nivel definitivo que se reporta al usuario lo confirma
después el evaluador LLM.

Calibración (medida sobre corpus reales en español, 8 consultas documentadas y 12 ajenas):

- El cross-encoder es la ÚNICA señal que discrimina. BM25 y el vector se solapan por
  completo entre lo documentado y lo ajeno (vector 0.31–0.52 frente a 0.33–0.40), así que
  no sirven para decidir por sí solos.
- Su escala depende del idioma. En inglés separa 0.79–0.99 frente a ~0.00; en español
  ordena igual de bien, pero comprime los valores: lo documentado cae en 1e-4 … 7.7e-1 y
  lo ajeno en 0 … 7.9e-3. Por eso los cortes son tan bajos y no porcentajes redondos.
- Las dos franjas se solapan entre 1e-4 y 7.9e-3, y ningún corte único las separa. Esa
  franja se declara LEVEL_C (hay material relacionado, el dato concreto puede no estar):
  el sistema responde diciendo que la evidencia es débil, en vez de callar o de afirmar.
- Por debajo de 5e-4 el modelo deja de discriminar: consultas documentadas y ajenas
  puntúan ambas 1e-4. Ahí la única respuesta honesta es abstenerse, aunque eso deje fuera
  alguna pregunta con respuesta (se asume el falso negativo antes que inventar).
"""
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

# Niveles epistémicos auditables
LEVEL_A_SUFFICIENT = "LEVEL_A_SUFFICIENT"
LEVEL_B_PARTIAL = "LEVEL_B_PARTIAL"
LEVEL_C_RELATED_INSUFFICIENT = "LEVEL_C_RELATED_INSUFFICIENT"
LEVEL_D_NO_EVIDENCE = "LEVEL_D_NO_EVIDENCE"

# Umbrales de evidencia (ver la calibración en el encabezado del módulo)
STRONG_RERANK = 0.50
WEAK_RERANK = 0.05      # por debajo, la evidencia no basta por sí sola
GRAY_RERANK = 0.0005    # por debajo, el cross-encoder ya no distingue señal de ruido
STRONG_BM25 = 4.5
STRONG_VECTOR = 0.60
WEAK_VECTOR = 0.30


# Frases con las que el sistema reconoce no tener la respuesta. Se comparten con el
# evaluador y con la ruta rápida: una abstención nunca puede anunciarse como respaldada.
ABSTENTION_MARKERS = (
    "no consta",
    "no se dispone",
    "no existe registro",
    "no se encuentra",
    "no contiene",
    "no aparece en la documentación",
    "no hay información",
    "no está especificad",
    "no se especifica",
    "no está documentad",
    "no figura en",
)


def is_abstention(text: str) -> bool:
    """True si la respuesta admite no tener el dato en lugar de aportarlo."""
    if not text:
        return False
    bajo = text.lower()
    return any(marca in bajo for marca in ABSTENTION_MARKERS)


def _best_scores(passages: List[Dict[str, Any]]) -> Dict[str, float]:
    """
    Mejor puntuación por canal. El cross-encoder solo cuenta si realmente se ejecutó:
    en modo bypass el campo reranker_score contiene el RRF, que no es comparable.
    """
    rerank_real = [
        float(p.get("reranker_score") or 0.0)
        for p in passages
        if not str(p.get("rerank_rationale", "")).startswith(("Bypass", "Fallback"))
    ]
    return {
        "rerank": max(rerank_real) if rerank_real else -1.0,  # -1 = no disponible
        "bm25": max((float(p.get("bm25_score") or 0.0) for p in passages), default=0.0),
        "vector": max((float(p.get("vector_score") or p.get("similarity") or 0.0) for p in passages), default=0.0),
    }


def classify_epistemic_uncertainty(
    vector_passages: List[Dict[str, Any]],
    graph_nodes: List[Dict[str, Any]],
    graph_paths: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Determina cuánta evidencia respalda la consulta antes de generar la respuesta.
    """
    if not vector_passages and not graph_nodes:
        return {
            "uncertainty_level": LEVEL_D_NO_EVIDENCE,
            "reason": "No se recuperaron pasajes documentales ni nodos del grafo para esta consulta.",
        }

    s = _best_scores(vector_passages)

    strong = (
        (s["rerank"] >= STRONG_RERANK)
        or s["bm25"] >= STRONG_BM25
        or s["vector"] >= STRONG_VECTOR
        or (s["rerank"] < 0 and len(graph_paths) >= 2)  # sin cross-encoder, el grafo decide
    )
    # El cross-encoder manda: un BM25 alto solo indica coincidencia léxica (palabras comunes),
    # no que el fragmento hable de lo que se pregunta.
    if s["rerank"] >= 0:
        sin_evidencia = s["rerank"] < GRAY_RERANK and not graph_paths
        zona_gris = GRAY_RERANK <= s["rerank"] < WEAK_RERANK and not graph_paths
    else:
        sin_evidencia = s["bm25"] < 1.0 and s["vector"] < WEAK_VECTOR and not graph_paths
        zona_gris = False

    if sin_evidencia:
        level = LEVEL_D_NO_EVIDENCE
        reason = (
            f"Los fragmentos recuperados no guardan relación con la consulta "
            f"(relevancia cross-encoder {s['rerank']:.4f}, BM25 {s['bm25']:.2f})."
        )
    elif zona_gris:
        level = LEVEL_C_RELATED_INSUFFICIENT
        reason = (
            f"Se recuperó material del mismo ámbito, pero la relevancia es baja "
            f"(cross-encoder {s['rerank']:.4f}): el dato concreto puede no estar documentado."
        )
    elif strong:
        level = LEVEL_A_SUFFICIENT
        reason = (
            f"Evidencia directa recuperada (cross-encoder {s['rerank']:.2f}, BM25 {s['bm25']:.2f}, "
            f"{len(vector_passages)} pasajes, {len(graph_paths)} caminos de grafo)."
        )
    else:
        level = LEVEL_B_PARTIAL
        reason = (
            f"Evidencia parcial o indirecta (cross-encoder {s['rerank']:.2f}, BM25 {s['bm25']:.2f}): "
            f"puede faltar el dato concreto solicitado."
        )

    logger.info(f"Clasificación epistémica determinista -> {level} | {reason}")
    return {"uncertainty_level": level, "reason": reason}
