"""
Servicio de Generación Dinámica de Preguntas Sugeridas (Adaptive Suggestion Service).
Analiza los documentos activos en ChromaDB y las entidades del Grafo en Neo4j
para generar preguntas contextuales 100% adaptadas al corpus del cliente.
"""
import json
import asyncio
import logging
import hashlib
from typing import List, Dict, Any

from app.core.llm_gateway import get_llm_response
from app.core.auth import DEFAULT_TENANT_ID
from app.db.hybrid_retriever import hybrid_retriever
from app.db.graph_manager import graph_manager

logger = logging.getLogger(__name__)

# Caché en memoria para evitar llamadas redundantes
_SUGGESTION_CACHE: Dict[str, List[Dict[str, Any]]] = {}

# Con la base vacía no se sugiere nada: cualquier pregunta se contestaría con una
# abstención, y la primera impresión del usuario sería un fallo del sistema. La
# interfaz muestra en su lugar una invitación a subir el primer documento.
SIN_CORPUS: List[Dict[str, Any]] = []


def _fetch_top_entities(tenant_id: str = DEFAULT_TENANT_ID) -> List[str]:
    """Entidades más conectadas del grafo de la organización (consulta síncrona a Neo4j)."""
    with graph_manager.connect().session() as session:
        records = session.run("""
        MATCH (n:Entity {tenant_id: $tenant_id})
        OPTIONAL MATCH (n)-[r]->(:Entity {tenant_id: $tenant_id})
        WITH n, count(r) AS degree
        ORDER BY degree DESC
        LIMIT 20
        RETURN n.name AS name, coalesce(n.type, n.entity_type, 'Entity') AS type
        """, tenant_id=tenant_id).data()
    return [f"{r['name']} ({r['type']})" for r in records if r.get("name")]


def _compute_corpus_hash(doc_names: List[str], node_count: int, tenant_id: str) -> str:
    key = f"{tenant_id}|" + "|".join(sorted(doc_names)) + f"|nodes:{node_count}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


async def get_adaptive_suggestions(tenant_id: str = DEFAULT_TENANT_ID) -> List[Dict[str, Any]]:
    """
    Genera 4 preguntas sugeridas dinámicas basadas en los documentos y entidades de la organización.
    """
    docs_list = await asyncio.to_thread(hybrid_retriever.list_indexed_documents, tenant_id)
    if not docs_list:
        return SIN_CORPUS

    doc_names = [d["filename"] for d in docs_list]

    try:
        top_entities = await asyncio.to_thread(_fetch_top_entities, tenant_id)
    except Exception as e:
        logger.warning(f"No se pudieron obtener entidades para sugerencias: {e}")
        top_entities = []

    corpus_hash = _compute_corpus_hash(doc_names, len(top_entities), tenant_id)
    if corpus_hash in _SUGGESTION_CACHE:
        return _SUGGESTION_CACHE[corpus_hash]

    # Generar sugerencias dinámicas con LLM
    docs_preview = ", ".join(doc_names[:10])
    entities_preview = ", ".join(top_entities[:15])

    messages = [
        {
            "role": "system",
            "content": (
                "Eres un diseñador de experiencias de IA y asistente corporativo experto. "
                "Tu objetivo es proponer 4 preguntas de ejemplo variadas, concretas, profesionales y relevantes "
                "que un usuario querría hacerle al asistente sobre los documentos indexados en el sistema.\n"
                "REGLAS:\n"
                "1. Analiza los nombres de los documentos y las entidades clave proporcionadas para deducir el tema real del corpus (ej. tecnología, legal, recursos humanos, proyectos, etc.).\n"
                "2. Las preguntas deben ser altamente específicas a los temas detectados en los nombres de archivo y entidades.\n"
                "3. Incluye siempre una pregunta global o comparativa (ej. '¿Qué tienen en común los documentos?' o '¿Cuáles son los temas principales?').\n"
                "4. Responde ÚNICAMENTE en JSON válido con una lista de 4 objetos:\n"
                "[\n"
                "  {\n"
                "    \"category\": \"Categoría corta (ej. Tecnología / Legal / HR / Proyecto / ...)\",\n"
                "    \"icon\": \"Emoji representativo (ej. 🚀, ⚖️, 🧑‍💻, 🛡️, 💡)\",\n"
                "    \"text\": \"Texto breve y conciso para el botón (máx 6 palabras)\",\n"
                "    \"query\": \"Pregunta completa detallada que se enviará al asistente\"\n"
                "  }\n"
                "]"
            )
        },
        {
            "role": "user",
            "content": f"Documentos activos en el corpus:\n{docs_preview}\n\nEntidades clave en el grafo relacional:\n{entities_preview if entities_preview else 'No hay entidades disponibles, básate solo en los títulos de los documentos.'}"
        }
    ]

    try:
        raw = await get_llm_response(messages, temperature=0.2, max_tokens=400)
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[1]
            cleaned = cleaned.rsplit("```", 1)[0]
        parsed = json.loads(cleaned.strip())

        if isinstance(parsed, list) and len(parsed) >= 3:
            _SUGGESTION_CACHE[corpus_hash] = parsed[:4]
            return parsed[:4]
    except Exception as e:
        logger.error(f"Error generando sugerencias adaptativas con LLM: {e}")

    # Fallback inteligente basado en nombres de documentos si falla el LLM
    fallback = [
        {
            "category": "Visión General",
            "icon": "📑",
            "text": "¿Qué tienen en común los documentos?",
            "query": "¿Qué tienen en común los documentos subidos y cuáles son sus temáticas principales?"
        },
        {
            "category": "Contenido",
            "icon": "📊",
            "text": f"Resumen de {doc_names[0][:20]}",
            "query": f"¿Cuáles son los puntos clave y conceptos principales explicados en {doc_names[0]}?"
        }
    ]
    if len(doc_names) > 1:
        fallback.append({
            "category": "Análisis Detallado",
            "icon": "💡",
            "text": f"Detalles de {doc_names[1][:20]}",
            "query": f"Explica en detalle los conceptos y normativas tratados en {doc_names[1]}."
        })
    fallback.append({
        "category": "Grafo Relacional",
        "icon": "🕸️",
        "text": "Entidades y relaciones clave",
        "query": "¿Qué entidades, personas o conceptos principales conectan los documentos en el grafo de conocimiento?"
    })

    _SUGGESTION_CACHE[corpus_hash] = fallback
    return fallback
