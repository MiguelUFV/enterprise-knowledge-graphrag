"""
Operaciones sobre la Base de Conocimiento: ingesta de documentos (ChromaDB + BM25 + Neo4j)
y restablecimiento a estado limpio (Clean Slate).
"""
import asyncio
import logging
from typing import Dict, Any

from app.core.auth import DEFAULT_TENANT_ID
from app.core.llm_gateway import get_llm_response
from app.core.llm_json import parse_llm_json
from app.core.chunking import structured_chunker
from app.core.entity_quality import filter_entities, build_canonicalizer
from app.db.graph_manager import graph_manager
from app.db.cache_manager import cache_manager
from app.db.hybrid_retriever import hybrid_retriever
from app.core.query_router import query_router

logger = logging.getLogger(__name__)

MAX_WINDOW_CHARS = 10000
MAX_EXTRACTION_WINDOWS = 20  # Tope de coste LLM: ~200K caracteres por documento

EXTRACTION_PROMPT = """Eres un extractor de Grafos de Conocimiento Empresarial. Extraes SOLO entidades identificables con nombre propio.

QUÉ ES UNA ENTIDAD (extráela):
- Personas con nombre: "María López", "Dr. Kenji Watanabe"
- Organizaciones: "Acme Industrial S.A.", "Delta Logistics GmbH"
- Productos, proyectos y tecnologías con nombre: "Proyecto Aurora", "Plataforma Helios", "Protocolo Centinela"
- Documentos, contratos, incidencias y normas con identificador: "CNT-2024-118", "INC-2025-042", "ISO/IEC 27001", "Política de Teletrabajo 2025"
- Planes, programas, marcos y leyes con nombre propio: "Plan de Continuidad de Negocio (BCP)", "Reglamento General de Protección de Datos (RGPD)", "Programa de Formación Continua"
- Lugares e instalaciones concretas: "Planta de Zaragoza"

QUÉ NO ES UNA ENTIDAD (NO la extraigas nunca):
- Cifras, métricas o porcentajes: "300%", "1.250 €", "ROI", "KPI", "47 minutos"
- Roles o categorías genéricas sin nombre: "Employee", "Gerente de las instalaciones", "Direct Reports", "el cliente"
- Sintagmas descriptivos: "government and military sectors", "Employee and customer data", "auditoría externa"
- Conceptos abstractos sin identidad: "seguridad", "calidad", "cumplimiento normativo"

REGLAS:
1. Usa el nombre tal y como aparece en el texto, sin artículos ("Proyecto Aurora", no "el Proyecto Aurora") y sin pegarle su descripción detrás ("ISO/IEC 27001:2022", no "ISO/IEC 27001:2022 Information Security Management").
2. 'entity_type' debe ser uno de: Person, Organization, Product, Service, Project, Technology, Policy, Contract, Incident, Location, Document, Role, Event, Certification.
3. Los datos numéricos (precios, plazos, porcentajes, fechas) van en 'properties' de la entidad a la que pertenecen, NUNCA como entidad propia.
4. Una relación conecta DOS entidades con nombre propio. El tipo va en MAYÚSCULAS_CON_GUION_BAJO y describe un verbo: HAS_CEO, SIGNED_WITH, USES, MANAGED_BY, APPLIES_TO, PART_OF, LOCATED_IN, RESOLVED_BY. Si el texto vincula dos entidades, la relación es OBLIGATORIA: un grafo sin aristas no sirve de nada.
5. DIRECCIÓN: la relación se lee como una frase 'entidad + relación + destino'. Si Rosa dirige Acme, la entidad es "Acme" y su relación es MANAGED_BY hacia "Rosa" ("Acme es gestionada por Rosa"); NUNCA al revés. Igual: "Acme" HAS_CERTIFICATION "ISO 22000", "Plan 2026" FINANCED_BY "Banco del Norte", "Acme" AUDITED_BY "AENOR". Declara cada vínculo UNA sola vez, en una sola dirección: no repitas la misma relación desde las dos entidades.
6. SÉ EXHAUSTIVO: recorre el documento entero y extrae TODAS las entidades que cumplan los criterios (lo normal son entre 8 y 25 en un documento corporativo), no solo las más evidentes.
7. Si el texto no contiene entidades con nombre propio, devuelve {"entities": []}.

Responde ÚNICAMENTE con este JSON:
{
  "entities": [
    {
      "entity_name": "Nombre propio exacto",
      "entity_type": "Uno del vocabulario",
      "properties": {"descripcion": "qué es, en una frase", "detalle": "datos concretos: cifras, fechas, importes"},
      "relations": [
        {"target_name": "Otra entidad con nombre propio", "target_type": "Tipo", "relation_type": "VERBO_EN_MAYUSCULAS", "properties": {}}
      ]
    }
  ]
}"""


def _parse_entities_json(raw: str) -> list:
    data = parse_llm_json(raw)
    if isinstance(data, dict):
        return data.get("entities", [])
    return data if isinstance(data, list) else []


async def process_file_content(
    filename: str, content: str, tenant_id: str = DEFAULT_TENANT_ID
) -> Dict[str, Any]:
    """Indexa el documento en ChromaDB/BM25 y extrae entidades y relaciones hacia Neo4j."""
    logger.info(f"Procesando documento [{tenant_id}]: '{filename}' ({len(content)} caracteres)...")

    # 1. Fragmentación e indexación vectorial + léxica
    structured_chunks = structured_chunker.chunk_document(content, filename)
    # to_dict() aporta filename, chunk_index, header_path y char_count: sin ellos las
    # respuestas no pueden citar de qué documento y sección proceden.
    chunks_payload = [
        {"doc_id": c.chunk_id, "text": c.text, "metadata": c.to_dict()["metadata"]}
        for c in structured_chunks
    ]
    await asyncio.to_thread(hybrid_retriever.add_document_chunks_batch, chunks_payload, tenant_id)

    # 2. Extracción LLM de entidades por ventanas en paralelo (documento completo hasta el tope)
    text_windows = [content[i:i + MAX_WINDOW_CHARS] for i in range(0, len(content), MAX_WINDOW_CHARS)]
    if len(text_windows) > MAX_EXTRACTION_WINDOWS:
        logger.warning(
            f"'{filename}' tiene {len(text_windows)} ventanas; el grafo se construye con las primeras "
            f"{MAX_EXTRACTION_WINDOWS}. El texto completo sigue indexado en ChromaDB/BM25."
        )
        text_windows = text_windows[:MAX_EXTRACTION_WINDOWS]

    sem = asyncio.Semaphore(5)

    async def _extract_window(idx: int, window_text: str) -> list:
        async with sem:
            messages = [
                {"role": "system", "content": EXTRACTION_PROMPT},
                {"role": "user", "content": f"Documento '{filename}' [Parte {idx+1}/{len(text_windows)}]:\n\n{window_text}"}
            ]
            try:
                raw_json = await get_llm_response(messages, temperature=0.0, max_tokens=4000)
                return _parse_entities_json(raw_json)
            except Exception as e:
                logger.warning(f"Aviso extrayendo entidades parte {idx+1} de '{filename}': {e}.")
                return []

    extraction_results = await asyncio.gather(*[
        _extract_window(idx, window) for idx, window in enumerate(text_windows)
    ])
    raw_entities = [ent for ents in extraction_results for ent in ents if isinstance(ent, dict)]

    # Control de calidad: descarta métricas, roles genéricos y sintagmas descriptivos,
    # y unifica las variantes con las entidades que ya existen en el grafo.
    existing_names = await asyncio.to_thread(graph_manager.get_entity_names, 10000, tenant_id)
    canonicalize = build_canonicalizer(existing_names) if existing_names else None
    all_entities, rejected = filter_entities(raw_entities, canonicalize=canonicalize)
    if rejected:
        muestra = ", ".join(f"'{n}' ({r})" for n, r in rejected[:5])
        logger.info(
            f"Calidad de extracción en '{filename}': {len(all_entities)} entidades válidas, "
            f"{len(rejected)} descartadas. Ejemplos: {muestra}"
        )

    # 3. Ingesta masiva en Neo4j (upsert_entities_batch ya incluye fallback secuencial)
    ingested_count = 0
    if all_entities:
        ingested_count = await asyncio.to_thread(
            graph_manager.upsert_entities_batch, all_entities, filename, tenant_id
        )

    warning = None
    if all_entities and ingested_count == 0:
        warning = "El texto se indexó, pero no se pudo guardar ninguna entidad en Neo4j (¿base de datos no disponible?)."
    elif not all_entities:
        warning = "No se extrajeron entidades para el grafo (fallo o respuesta vacía del LLM)."
    if warning:
        logger.warning(f"'{filename}': {warning}")

    logger.info(f"Ingesta completada: {ingested_count}/{len(all_entities)} entidades y {len(structured_chunks)} fragmentos para '{filename}'.")
    return {
        "success": True,
        "filename": filename,
        "entities_found": len(all_entities),
        "entities_ingested": ingested_count,
        "chunks_indexed": len(structured_chunks),
        "warning": warning
    }


def reset_all(tenant_id: str = DEFAULT_TENANT_ID) -> bool:
    """
    Vacía la base de conocimiento de UNA organización: sus entidades del grafo, sus
    fragmentos en ChromaDB, su índice BM25 y su caché semántica. Los datos del resto
    de organizaciones no se tocan.
    """
    ok = True
    try:
        driver = graph_manager.connect()
        with driver.session() as session:
            session.run("MATCH (n:Entity {tenant_id: $tenant_id}) DETACH DELETE n", tenant_id=tenant_id)
    except Exception as e:
        logger.error(f"Fallo al vaciar Neo4j: {e}")
        ok = False

    ok = hybrid_retriever.reset_collection(tenant_id=tenant_id) and ok
    ok = cache_manager.invalidate_all(tenant_id=tenant_id) and ok

    query_router.dynamic_entities.pop(tenant_id, None)
    query_router._synced_tenants.discard(tenant_id)

    from app.services.ingest_task_manager import ingest_task_manager
    ingest_task_manager.clear_all()

    logger.info(f"Reset de base de conocimiento de '{tenant_id}' finalizado (ok={ok}).")
    return ok
