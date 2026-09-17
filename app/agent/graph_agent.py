import json
import asyncio
import logging
from typing import TypedDict, List, Dict, Any, Optional, Awaitable

from langgraph.graph import StateGraph, END
from langsmith import traceable

from app.core.auth import DEFAULT_TENANT_ID
from app.core.llm_gateway import get_llm_response
from app.core.llm_json import parse_llm_json
from app.core.evidence import (
    classify_epistemic_uncertainty,
    is_abstention,
    LEVEL_A_SUFFICIENT,
    LEVEL_C_RELATED_INSUFFICIENT,
    LEVEL_D_NO_EVIDENCE
)
from app.core.guardrails import UnsafeQueryError
from app.core.evaluator import evaluate_rag_response
from app.db.hybrid_retriever import hybrid_retriever
from app.core.query_router import query_router

logger = logging.getLogger(__name__)

# Un reintento basta: en las pruebas, el segundo casi nunca mejoró la respuesta y costaba ~15 s.
MAX_RETRIES = 1
# Por debajo de esta confianza merece la pena reintentar; por encima, no compensa la latencia.
RETRY_CONFIDENCE_THRESHOLD = 0.45


# ---------------------------------------------------------------------------
# Estado Tipado y Auditable del Agente (TypedDict)
# ---------------------------------------------------------------------------
class AgentState(TypedDict):
    """Estado compartido que fluye entre los nodos del grafo LangGraph."""
    question: str                          # Pregunta original del usuario
    safety_gate: Optional[Awaitable]       # Guardrail LLM en curso; se espera antes de generar
    route: str                             # FAST_PATH, HYBRID_PATH o FULL_GRAPH_RAG
    is_global_query: bool                  # Consulta transversal de corpus (inyecta visión holística)
    is_fast_path_verified: bool            # True si Fast Path superó el Post-Retrieval Guard
    entities: List[str]                    # Entidades extraídas de la pregunta
    additional_queries: List[str]          # Consultas para Multi-Query en ChromaDB
    hybrid_context: Dict[str, Any]         # Pasajes, Nodos, Relaciones y Caminos (Paths)
    uncertainty_level: str                 # LEVEL_A, LEVEL_B, LEVEL_C o LEVEL_D
    uncertainty_reason: str                # Explicación del nivel de incertidumbre
    response: str                          # Respuesta generada por el LLM
    evidence_grounding: Dict[str, Any]     # Mapeo claim -> [Documento, Camino Grafo]
    is_faithful: bool                      # Resultado de no-alucinación y fidelidad
    faithfulness_score: float              # Puntuación de veracidad (0.0 a 1.0)
    relevancy_score: float                 # Puntuación de relevancia a la consulta
    grounding_ratio: float                 # Porcentaje de afirmaciones con evidencia
    confidence_score: float                # Índice compuesto no lineal (0.0 a 1.0)
    critique: str                          # Retroalimentación del evaluador
    missing_aspects: str                   # Elementos faltantes para la auto-corrección
    unsupported_claims: List[str]          # Lista de afirmaciones no demostradas
    retry_strategy_log: List[Dict[str, Any]] # Registro de diagnósticos y cambios por retry
    iteration: int                         # Contador de iteraciones
    tenant_id: str                         # Organización propietaria de los datos consultados


# ---------------------------------------------------------------------------
# Nodo 1: Extracción de Entidades y Planificación de Búsqueda
# ---------------------------------------------------------------------------
@traceable(name="extract_entities_and_plan", run_type="chain")
async def extract_entities_and_plan(state: AgentState) -> AgentState:
    """
    Identifica las entidades principales y determina la ruta adaptativa de ejecución.
    """
    question = state["question"]
    iteration = state.get("iteration", 0)

    # 1. Clasificación Pre-Retrieval mediante QueryRouter determinista (puede consultar Neo4j)
    tenant_id = state.get("tenant_id") or DEFAULT_TENANT_ID
    route_info = await asyncio.to_thread(query_router.classify_pre_retrieval, question, tenant_id)
    route = route_info["route"]

    logger.info(f"[Iteración {iteration}] QueryRouter -> {route} ({route_info['reason']}) para: '{question[:80]}...'")

    # Si es FAST_PATH, evitamos la llamada LLM de extracción de entidades (ahorro ~1.2s)
    if route == "FAST_PATH":
        detected_entities = list(set(route_info.get("detected_entities", []) + route_info.get("detected_ids", [])))
        return {
            **state,
            "route": route,
            "entities": detected_entities,
            "additional_queries": [],
            "retry_strategy_log": [],
            "iteration": iteration,
            "is_fast_path_verified": False
        }

    # Si el router ya reconoció entidades del grafo o identificadores, no hace falta
    # gastar una llamada LLM (~1,6 s) en volver a extraerlas.
    prerouted = list(dict.fromkeys(route_info.get("detected_entities", []) + route_info.get("detected_ids", [])))
    if prerouted:
        logger.info(f"Entidades aportadas por el QueryRouter (sin llamada LLM): {prerouted}")
        return {
            **state,
            "route": route,
            "is_global_query": route_info.get("is_global_query", False),
            "entities": prerouted,
            "additional_queries": [],
            "retry_strategy_log": [],
            "iteration": iteration,
            "is_fast_path_verified": False
        }

    # Para HYBRID_PATH o FULL_GRAPH_RAG sin entidades conocidas, extracción con LLM
    messages = [
        {
            "role": "system",
            "content": (
                "Eres un extractor experto de entidades y relaciones empresariales. "
                "Extrae todos los nombres propios (empresas, personas, productos, tecnologías, contratos, incidencias, políticas) de la consulta. "
                "Responde ÚNICAMENTE en JSON: {\"entities\": [\"Entidad1\", \"Entidad2\"]}. "
                "No agregues texto explicativo fuera del JSON."
            )
        },
        {
            "role": "user",
            "content": question
        }
    ]

    raw_response = await get_llm_response(messages, temperature=0.0, max_tokens=200)

    try:
        entities = parse_llm_json(raw_response).get("entities", [])
    except Exception:
        entities = [word for word in question.split() if len(word) > 3]

    combined_entities = list(set(entities + route_info.get("detected_entities", []) + route_info.get("detected_ids", [])))

    return {
        **state,
        "route": route,
        "is_global_query": route_info.get("is_global_query", False),
        "entities": combined_entities,
        "additional_queries": [],
        "retry_strategy_log": [],
        "iteration": iteration,
        "is_fast_path_verified": False
    }


# ---------------------------------------------------------------------------
# Nodo 2: Recuperación Híbrida Multi-Hop y Clasificación Epistémica
# ---------------------------------------------------------------------------
@traceable(name="retrieve_context", run_type="retriever")
async def retrieve_context(state: AgentState) -> AgentState:
    """
    Recuperación adaptativa: Fast Path, Hybrid Path o Full Graph RAG.
    """
    question = state["question"]
    entities = state.get("entities", [])
    additional_queries = state.get("additional_queries", [])
    iteration = state.get("iteration", 0)
    route = state.get("route", "HYBRID_PATH")
    is_global_query = state.get("is_global_query", False)

    # =========================================================================
    # RAMA 1: FAST PATH (BM25 + Vector Top-3, sin Neo4j ni Cross-Encoder)
    # =========================================================================
    if route == "FAST_PATH" and iteration == 0:
        bm25_res, vec_res = await asyncio.gather(
            asyncio.to_thread(hybrid_retriever.search_bm25, question, 3),
            asyncio.to_thread(hybrid_retriever.search_vector_store, question, 3)
        )

        detected_ids = [e for e in entities if any(c.isdigit() for c in e) or "-" in e]
        is_sufficient, reason, passages = query_router.verify_fast_path_sufficiency(
            query=question,
            bm25_results=bm25_res,
            vector_results=vec_res,
            detected_ids=detected_ids
        )

        if is_sufficient:
            logger.info(f"Fast Path APROBADO ({reason}). Saltando Neo4j, Cross-Encoder y Clasificador LLM.")
            unified_sources = [p.get("metadata", {}).get("filename", "doc") for p in passages]
            hybrid_context = {
                "vector_passages": passages,
                "graph_nodes": [],
                "graph_relationships": [],
                "graph_paths": [],
                "unified_sources": list(set(unified_sources))
            }
            return {
                **state,
                "hybrid_context": hybrid_context,
                "uncertainty_level": LEVEL_A_SUFFICIENT,
                "uncertainty_reason": f"Evidencia directa verificada por Fast Path ({reason})",
                "is_fast_path_verified": True
            }
        else:
            logger.info(f"Fast Path INSUFICIENTE ({reason}). Escalando automáticamente a HYBRID_PATH.")
            route = "HYBRID_PATH"
            retry_log = list(state.get("retry_strategy_log", []))
            retry_log.append({
                "escalation": "FAST_PATH_TO_HYBRID",
                "reason": reason
            })
            state["retry_strategy_log"] = retry_log
            state["route"] = "HYBRID_PATH"

    # =========================================================================
    # RAMA 2: HYBRID PATH o FULL GRAPH RAG
    # =========================================================================
    # Profundidad adaptativa: Hybrid usa 1 hop en Neo4j; Full usa 2 (o 3 en retry)
    if route == "HYBRID_PATH":
        top_k = 5
        max_hops = 1
    else:  # FULL_GRAPH_RAG
        top_k = 5 if iteration == 0 else 8
        max_hops = 2 if iteration == 0 else 3

    try:
        hybrid_context = await asyncio.to_thread(
            hybrid_retriever.retrieve_hybrid_context,
            query=question,
            entities=entities,
            top_k_vectors=top_k,
            max_hops=max_hops,
            additional_queries=additional_queries,
            is_global_query=is_global_query,
            tenant_id=state.get("tenant_id") or DEFAULT_TENANT_ID
        )
    except Exception as e:
        logger.error(f"Error en recuperación híbrida: {e}")
        hybrid_context = {
            "vector_passages": [],
            "graph_nodes": [],
            "graph_relationships": [],
            "graph_paths": [],
            "unified_sources": []
        }

    # Clasificar nivel de incertidumbre epistémica (determinista, sin llamada LLM)
    uncertainty_info = classify_epistemic_uncertainty(
        vector_passages=hybrid_context.get("vector_passages", []),
        graph_nodes=hybrid_context.get("graph_nodes", []),
        graph_paths=hybrid_context.get("graph_paths", [])
    )

    return {
        **state,
        "hybrid_context": hybrid_context,
        "uncertainty_level": uncertainty_info["uncertainty_level"],
        "uncertainty_reason": uncertainty_info["reason"],
        "is_fast_path_verified": False
    }


# ---------------------------------------------------------------------------
# Nodo 3: Generación de Respuesta con Evidence Grounding e Incertidumbre
# ---------------------------------------------------------------------------
@traceable(name="generate_response", run_type="chain")
async def generate_response(state: AgentState) -> AgentState:
    """
    Genera la respuesta aplicando reglas estrictas según el nivel de incertidumbre (A, B, C o D).
    """
    question = state["question"]
    hybrid_context = state.get("hybrid_context", {})
    uncertainty_level = state.get("uncertainty_level", LEVEL_A_SUFFICIENT)
    uncertainty_reason = state.get("uncertainty_reason", "")
    iteration = state.get("iteration", 0)
    critique = state.get("critique", "")
    unsupported = state.get("unsupported_claims", [])

    # Puerta de seguridad: el guardrail LLM corrió en paralelo con la recuperación,
    # pero nada se genera hasta que confirma que la consulta es segura.
    gate = state.get("safety_gate")
    if gate is not None and not await gate:
        raise UnsafeQueryError("La consulta fue rechazada por los guardrails de seguridad.")

    # Protocolo especial para Nivel D (Ausencia total de evidencia)
    if uncertainty_level == LEVEL_D_NO_EVIDENCE:
        abstention_text = (
            f"No consta en el corpus documental ni en el grafo de conocimiento de la empresa información sobre lo solicitado en la consulta. "
            f"({uncertainty_reason})"
        )
        return {**state, "response": abstention_text}

    # Formatear contexto enriquecido
    vector_passages_str = "\n---\n".join([
        f"Pasaje [{chunk.get('metadata', {}).get('filename', 'doc')}#{chunk.get('metadata', {}).get('header_path', '')}]: {chunk.get('text')}"
        for chunk in hybrid_context.get("vector_passages", [])
    ])

    graph_paths = hybrid_context.get("graph_paths", [])
    graph_paths_str = "\n".join([
        f"- Camino: {p.get('path_str', '')}"
        for p in graph_paths
    ])

    graph_nodes = hybrid_context.get("graph_nodes", [])
    graph_nodes_str = "\n".join([
        f"- {node.get('properties', {}).get('name', 'Nodo')} ({node.get('properties', {}).get('type', 'Entity')}): {node.get('properties', {}).get('descripcion', '')} {node.get('properties', {}).get('detalle', '')}"
        for node in graph_nodes[:15]
    ])

    system_instruction = (
        "Eres un asistente de IA de máxima rigurosidad y precisión empresarial. "
        "Tu objetivo es responder de forma clara, directa y estrictamente fundamentada en la evidencia provista.\n\n"
        "REGLAS FUNDAMENTALES DE EVIDENCE GROUNDING:\n"
        "1. Cita únicamente hechos, números, fechas, personas y relaciones presentes en el contexto.\n"
        "2. Si la evidencia es PARCIAL (Nivel B o C), responde con lo que está demostrado y declara expresamente qué parte no está documentada en lugar de inferirla.\n"
        "3. Aprovecha explícitamente los caminos del grafo para explicar relaciones de varios saltos (ej. Cliente -> Producto -> Tecnología).\n"
        "4. Si un dato no existe, di claramente 'No consta en la documentación'."
    )

    if iteration > 0 and (critique or unsupported):
        system_instruction += (
            f"\n\nADVERTENCIA DE REINTENTO (Iteración {iteration}):\n"
            f"El intento anterior falló por: {critique}.\n"
            f"Evita expresamente estas afirmaciones no demostradas: {json.dumps(unsupported, ensure_ascii=False)}."
        )

    user_prompt = f"""PREGUNTA DEL USUARIO:
{question}

NIVEL DE EVIDENCIA:
{uncertainty_level}

--- EVIDENCIA DOCUMENTAL (ChromaDB) ---
{vector_passages_str or 'Sin pasajes vectoriales disponibles'}

--- CAMINOS RELACIONALES DEL GRAFO (Neo4j Multi-Hop) ---
{graph_paths_str or 'Sin caminos relacionales directos'}

--- NODOS DEL GRAFO (Neo4j) ---
{graph_nodes_str or 'Sin nodos disponibles'}
"""

    messages = [
        {"role": "system", "content": system_instruction},
        {"role": "user", "content": user_prompt}
    ]

    response_text = await get_llm_response(messages, temperature=0.05, max_tokens=800)
    return {**state, "response": response_text}


# ---------------------------------------------------------------------------
# Nodo 4: Evaluación de Fidelidad, Relevancia y Grounding de Afirmaciones
# ---------------------------------------------------------------------------
@traceable(name="evaluate_and_ground", run_type="chain")
async def evaluate_and_ground(state: AgentState) -> AgentState:
    """
    Audita la respuesta generada mapeando cada afirmación contra la evidencia.
    En Fast Path o Nivel D, aplica verificación determinista para evitar latencia de LLM-Judge.
    """
    question = state["question"]
    response = state.get("response", "")
    hybrid_context = state.get("hybrid_context", {})
    uncertainty_level = state.get("uncertainty_level", LEVEL_A_SUFFICIENT)
    iteration = state.get("iteration", 0)

    # FAST PATH VERIFICADO: Validación determinista directa (<1ms, sin LLM-Judge)
    if state.get("is_fast_path_verified", False):
        passages = hybrid_context.get("vector_passages", [])

        # Sin evidencia recuperada, no se puede declarar grounding perfecto
        if not passages:
            return {
                **state,
                "evidence_grounding": {
                    "claims": [],
                    "grounding_ratio": 0.0,
                    "uncertainty_level": LEVEL_D_NO_EVIDENCE
                },
                "is_faithful": False,
                "faithfulness_score": 0.0,
                "relevancy_score": 0.0,
                "grounding_ratio": 0.0,
                "confidence_score": 0.0,
                "critique": "Fast Path sin evidencia recuperada: verificación NO superada.",
                "missing_aspects": "",
                "unsupported_claims": [response[:120]] if response else [],
                "iteration": iteration
            }

        # El modelo puede reconocer que el dato no está aunque la recuperación puntuara
        # alto. Anunciar entonces "evidencia suficiente" contradice la propia respuesta.
        if is_abstention(response):
            return {
                **state,
                "uncertainty_level": LEVEL_D_NO_EVIDENCE,
                "evidence_grounding": {
                    "claims": [],
                    "grounding_ratio": 1.0,
                    "uncertainty_level": LEVEL_D_NO_EVIDENCE
                },
                "is_faithful": True,
                "faithfulness_score": 1.0,
                "relevancy_score": 1.0,
                "grounding_ratio": 1.0,
                "confidence_score": 0.0,
                "critique": "Fast Path: la respuesta reconoce no disponer del dato; se reporta como abstención.",
                "missing_aspects": "",
                "unsupported_claims": [],
                "iteration": iteration
            }

        src = passages[0].get("metadata", {}).get("filename", "doc") if passages else "doc"
        # Calibración determinista honesta: el grounding escala con la evidencia recuperada
        # (3+ pasajes = grounding completo; nunca se asume confianza absoluta del LLM-Judge).
        evidence_count = len(passages)
        grounding = round(min(1.0, evidence_count / 3), 2)
        confidence = round(0.85 + 0.15 * grounding, 2)
        return {
            **state,
            "evidence_grounding": {
                "claims": [{"claim_text": response[:120], "is_supported": True, "citations": [src], "graph_references": []}],
                "grounding_ratio": grounding,
                "uncertainty_level": LEVEL_A_SUFFICIENT
            },
            "is_faithful": True,
            "faithfulness_score": grounding,
            "relevancy_score": grounding,
            "grounding_ratio": grounding,
            "confidence_score": confidence,
            "critique": "Verificado determinísticamente por Fast Path con calibración basada en evidencia.",
            "missing_aspects": "",
            "unsupported_claims": [],
            "iteration": iteration
        }

    # ABSTENCIÓN (NIVEL D): Calibración instantánea sin LLM-Judge
    if uncertainty_level == LEVEL_D_NO_EVIDENCE:
        return {
            **state,
            "evidence_grounding": {
                "claims": [],
                "grounding_ratio": 1.0,
                "uncertainty_level": LEVEL_D_NO_EVIDENCE
            },
            "is_faithful": True,
            "faithfulness_score": 1.0,
            "relevancy_score": 1.0,
            "grounding_ratio": 1.0,
            "confidence_score": 0.0,
            "critique": "Abstención legítima verificada: sin evidencia en corpus.",
            "missing_aspects": "",
            "unsupported_claims": [],
            "iteration": iteration
        }

    # Para HYBRID_PATH y FULL_GRAPH_RAG: Evaluación exhaustiva con LLM-Judge
    eval_result = await evaluate_rag_response(
        question=question,
        response=response,
        hybrid_context=hybrid_context,
        uncertainty_level=uncertainty_level
    )

    # El nivel que se reporta es el que confirma el auditor, no la heurística previa
    return {
        **state,
        "uncertainty_level": eval_result.get("uncertainty_level", uncertainty_level),
        "evidence_grounding": {
            "claims": eval_result.get("claims", []),
            "grounding_ratio": eval_result.get("grounding_ratio", 0.0),
            "uncertainty_level": eval_result.get("uncertainty_level", uncertainty_level)
        },
        "is_faithful": eval_result["is_faithful"],
        "faithfulness_score": eval_result["faithfulness_score"],
        "relevancy_score": eval_result["relevancy_score"],
        "grounding_ratio": eval_result.get("grounding_ratio", 0.0),
        "confidence_score": eval_result["confidence_score"],
        "critique": eval_result["critique"],
        "missing_aspects": eval_result["missing_aspects"],
        "unsupported_claims": eval_result["unsupported_claims"],
        "iteration": iteration
    }


# ---------------------------------------------------------------------------
# Nodo 5: Diagnóstico Específico y Refinamiento Adaptativo (Retry Inteligente)
# ---------------------------------------------------------------------------
@traceable(name="diagnose_and_refine", run_type="chain")
async def diagnose_and_refine(state: AgentState) -> AgentState:
    """
    Diagnostica qué tipo de evidencia falta (entidad, relación, documento o atributo)
    y cambia activamente la estrategia para la siguiente iteración.
    """
    question = state["question"]
    entities = state.get("entities", [])
    critique = state.get("critique", "")
    missing = state.get("missing_aspects", "")
    unsupported = state.get("unsupported_claims", [])
    iteration = state.get("iteration", 0)
    retry_log = list(state.get("retry_strategy_log", []))

    logger.info(f"Ejecutando diagnóstico de reintento [Iteración {iteration + 1}]. Motivo: {critique}")

    messages = [
        {
            "role": "system",
            "content": (
                "Eres un diagnosticador y planificador de recuperación RAG. "
                "Analiza qué evidencia falta (documentos, sinónimos, relaciones o entidades) y formula una nueva estrategia.\n"
                "Responde ÚNICAMENTE en JSON:\n"
                "{\n"
                "  \"missing_category\": \"MISSING_DOCUMENT | MISSING_RELATION | MISSING_ENTITY | MISSING_ATTRIBUTE\",\n"
                "  \"new_search_queries\": [\"query alternativa 1\", \"query alternativa 2\"],\n"
                "  \"expanded_entities\": [\"entidad_sinonimo_1\", \"entidad_sinonimo_2\"],\n"
                "  \"strategy_justification\": \"Explicación del cambio\"\n"
                "}"
            )
        },
        {
            "role": "user",
            "content": (
                f"Pregunta: {question}\n"
                f"Entidades previas: {json.dumps(entities, ensure_ascii=False)}\n"
                f"Crítica del evaluador: {critique}\n"
                f"Aspectos faltantes: {missing}\n"
                f"Afirmaciones no demostradas: {json.dumps(unsupported, ensure_ascii=False)}"
            )
        }
    ]

    try:
        raw_res = await get_llm_response(messages, temperature=0.2, max_tokens=300)
        diag = parse_llm_json(raw_res)

        new_queries = diag.get("new_search_queries", [])
        expanded_ents = diag.get("expanded_entities", [])
        combined_entities = list(set(entities + expanded_ents))

        retry_entry = {
            "retry_number": iteration + 1,
            "missing_category": diag.get("missing_category", "MISSING_DOCUMENT"),
            "new_queries": new_queries,
            "new_entities": expanded_ents,
            "justification": diag.get("strategy_justification", "")
        }
        retry_log.append(retry_entry)

    except Exception as e:
        logger.warning(f"Error en diagnóstico de reintento: {e}")
        combined_entities = entities
        new_queries = [f"{question} detalles", f"{question} especificacion"]
        retry_log.append({
            "retry_number": iteration + 1,
            "missing_category": "FALLBACK_EXPANSION",
            "new_queries": new_queries,
            "new_entities": [],
            "justification": "Expansión genérica por error en diagnóstico."
        })

    return {
        **state,
        "entities": combined_entities,
        "additional_queries": new_queries,
        "retry_strategy_log": retry_log,
        "iteration": iteration + 1
    }


# ---------------------------------------------------------------------------
# Enrutamiento Condicional
# ---------------------------------------------------------------------------
def faithfulness_router(state: AgentState) -> str:
    """
    Decide si finalizar o ejecutar el ciclo de diagnóstico y reintento.
    Solo se reintenta cuando hay margen real de mejora: un reintento cuesta ~15 s.
    """
    is_faithful = state.get("is_faithful", False)
    uncertainty_level = state.get("uncertainty_level", LEVEL_A_SUFFICIENT)
    iteration = state.get("iteration", 0)
    confidence = float(state.get("confidence_score", 0.0) or 0.0)

    # Abstención honesta: no hay nada que recuperar
    if uncertainty_level == LEVEL_D_NO_EVIDENCE:
        logger.info("Consulta sin evidencia en corpus (Nivel D): finalizando con abstención controlada.")
        return "end"

    # El dato concreto no está documentado: buscar de nuevo no lo va a encontrar
    if uncertainty_level == LEVEL_C_RELATED_INSUFFICIENT:
        logger.info("Nivel C (hecho no documentado): reintentar no aportaría evidencia nueva.")
        return "end"

    if is_faithful or iteration >= MAX_RETRIES:
        logger.info(f"Flujo LangGraph finalizado (Fiel={is_faithful}, Iteraciones={iteration}).")
        return "end"

    if confidence >= RETRY_CONFIDENCE_THRESHOLD:
        logger.info(f"Confianza {confidence:.2f} suficiente: se evita un reintento de ~15 s.")
        return "end"

    logger.info(f"Activando reintento inteligente (Iteración actual: {iteration}, confianza {confidence:.2f}).")
    return "refine"


# ---------------------------------------------------------------------------
# Compilación del Grafo LangGraph
# ---------------------------------------------------------------------------
def build_graph() -> StateGraph:
    workflow = StateGraph(AgentState)

    workflow.add_node("extract_entities_and_plan", extract_entities_and_plan)
    workflow.add_node("retrieve_context", retrieve_context)
    workflow.add_node("generate_response", generate_response)
    workflow.add_node("evaluate_and_ground", evaluate_and_ground)
    workflow.add_node("diagnose_and_refine", diagnose_and_refine)

    workflow.add_edge("extract_entities_and_plan", "retrieve_context")
    workflow.add_edge("retrieve_context", "generate_response")
    workflow.add_edge("generate_response", "evaluate_and_ground")

    workflow.add_conditional_edges(
        "evaluate_and_ground",
        faithfulness_router,
        {
            "end": END,
            "refine": "diagnose_and_refine"
        }
    )

    workflow.add_edge("diagnose_and_refine", "retrieve_context")

    workflow.set_entry_point("extract_entities_and_plan")

    return workflow


graph_workflow = build_graph()
compiled_graph = graph_workflow.compile()


# ---------------------------------------------------------------------------
# Punto de Entrada Principal
# ---------------------------------------------------------------------------
@traceable(name="run_agent_flow", run_type="chain")
async def run_agent_flow(
    query: str,
    safety_gate: Optional[Awaitable] = None,
    tenant_id: str = DEFAULT_TENANT_ID
) -> Dict[str, Any]:
    initial_state: AgentState = {
        "question": query,
        "safety_gate": safety_gate,
        "tenant_id": tenant_id,
        "route": "HYBRID_PATH",
        "is_global_query": False,
        "is_fast_path_verified": False,
        "entities": [],
        "additional_queries": [],
        "hybrid_context": {},
        "uncertainty_level": LEVEL_A_SUFFICIENT,
        "uncertainty_reason": "",
        "response": "",
        "evidence_grounding": {},
        "is_faithful": False,
        "faithfulness_score": 0.0,
        "relevancy_score": 0.0,
        "grounding_ratio": 0.0,
        "confidence_score": 0.0,
        "critique": "",
        "missing_aspects": "",
        "unsupported_claims": [],
        "retry_strategy_log": [],
        "iteration": 0
    }

    final_state = await compiled_graph.ainvoke(initial_state)

    hybrid_context = final_state.get("hybrid_context", {})
    sources = hybrid_context.get("unified_sources", [])

    # Invariante final, común a todas las rutas: si la respuesta reconoce que el dato
    # pedido no está, no puede anunciarse como evidencia suficiente. Da igual que la
    # recuperación puntuara alto o que el evaluador lo diera por bueno: la interfaz
    # mostraría "evidencia suficiente" junto a un "no está especificado".
    texto = final_state.get("response", "")
    nivel = final_state.get("uncertainty_level", LEVEL_A_SUFFICIENT)
    confianza = final_state.get("confidence_score", 0.85)
    if nivel == LEVEL_A_SUFFICIENT and is_abstention(texto):
        logger.info("La respuesta admite que falta el dato: se rebaja de nivel A a nivel C.")
        nivel = LEVEL_C_RELATED_INSUFFICIENT
        confianza = min(confianza, 0.5)

    return {
        "text": final_state.get("response", "No se pudo generar una respuesta."),
        "sources": sources if sources else ["hybrid:general_knowledge"],
        "confidence_score": confianza,
        "uncertainty_level": nivel,
        "evidence_grounding": final_state.get("evidence_grounding", {}),
        "faithfulness_score": final_state.get("faithfulness_score", 0.85),
        "relevancy_score": final_state.get("relevancy_score", 0.85),
        "graph_paths": hybrid_context.get("graph_paths", []),
        "retry_strategy_log": final_state.get("retry_strategy_log", []),
        "route": final_state.get("route", "HYBRID_PATH"),
        "is_fast_path_verified": final_state.get("is_fast_path_verified", False),
        "origin": "generation"
    }
