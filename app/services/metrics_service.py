"""
Servicio de métricas del sistema GraphRAG.
Recopila KPIs de rendimiento, uso de caché, estado de Neo4j y ChromaDB.
Diseñado para ser consultado por polling desde el frontend.
"""
import time
import asyncio
import logging
import threading
from collections import deque
from typing import Dict, Any

from app.core.auth import DEFAULT_TENANT_ID

logger = logging.getLogger(__name__)

# =========================================================================
# Almacén en memoria de métricas de rendimiento (thread-safe para asyncio)
# =========================================================================

# Rolling window de latencias de los últimos 100 requests a /ask
_latency_window: deque = deque(maxlen=100)

# Contadores globales
_total_requests: int = 0
_cache_hits: int = 0
_cache_misses: int = 0

# Lock para thread-safety en entornos multi-worker
_metrics_lock = threading.Lock()

# Timestamps
_service_start_time: float = time.time()


def record_request(latency_ms: float, from_cache: bool) -> None:
    """
    Registra una nueva petición al sistema.
    Llamar desde el endpoint /ask después de procesar la respuesta.

    :param latency_ms: Latencia de la petición en milisegundos.
    :param from_cache: True si la respuesta vino de la caché semántica.
    """
    global _total_requests, _cache_hits, _cache_misses
    with _metrics_lock:
        _latency_window.append(latency_ms)
        _total_requests += 1
        if from_cache:
            _cache_hits += 1
        else:
            _cache_misses += 1


def get_average_latency() -> float:
    """Retorna la latencia promedio en ms de los últimos 100 requests."""
    with _metrics_lock:
        if not _latency_window:
            return 0.0
        return round(sum(_latency_window) / len(_latency_window), 1)


def get_cache_hit_rate() -> float:
    """Retorna el porcentaje de hits de caché sobre el total de requests."""
    with _metrics_lock:
        if _total_requests == 0:
            return 0.0
        return round((_cache_hits / _total_requests) * 100, 1)


def get_uptime_seconds() -> int:
    """Retorna el tiempo de actividad del servicio en segundos."""
    return int(time.time() - _service_start_time)


async def get_neo4j_stats(tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
    """
    Obtiene estadísticas del grafo de la organización: nodos, relaciones y estado GDS.
    """
    try:
        from app.db.graph_manager import graph_manager
        stats = await asyncio.to_thread(graph_manager.get_graph_stats, tenant_id)
        gds_available = await asyncio.to_thread(graph_manager.check_gds_available)
        return {
            "nodes": stats.get("nodes", 0),
            "relationships": stats.get("relationships", 0),
            "gds_available": gds_available,
            "status": "connected"
        }
    except Exception as e:
        logger.warning(f"No se pudo obtener estadísticas de Neo4j: {e}")
        return {"nodes": 0, "relationships": 0, "gds_available": False, "status": "error"}


async def get_chroma_stats(tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
    """
    Obtiene estadísticas de ChromaDB: fragmentos indexados de la organización.
    """
    try:
        from app.db.hybrid_retriever import hybrid_retriever
        docs = await asyncio.to_thread(hybrid_retriever.list_indexed_documents, tenant_id)
        return {"chunks": sum(d["chunks_count"] for d in docs), "status": "connected"}
    except Exception as e:
        logger.warning(f"No se pudo obtener estadísticas de ChromaDB: {e}")
        return {"chunks": 0, "status": "error"}


def _format_uptime(seconds: int) -> str:
    """Convierte segundos a formato legible HH:MM:SS."""
    h = seconds // 3600
    m = (seconds % 3600) // 60
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


async def collect_all_metrics(tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
    """
    Recopila todas las métricas del sistema en un solo diccionario.
    Ésta es la función que llama el endpoint GET /api/metrics.
    """
    import os
    neo4j = await get_neo4j_stats(tenant_id)
    chroma = await get_chroma_stats(tenant_id)
    uptime_s = get_uptime_seconds()

    primary_model = os.getenv("PRIMARY_MODEL", "openrouter/openai/gpt-4o-mini")
    # Simplificar nombre del modelo para mostrar
    model_display = primary_model.split("/")[-1] if "/" in primary_model else primary_model

    return {
        "metrics": [
            {
                "name": "Latencia promedio",
                "value": f"{get_average_latency()} ms",
                "raw": get_average_latency(),
                "status": "ok" if get_average_latency() < 3000 else "warn" if get_average_latency() < 8000 else "error",
                "description": "Tiempo de respuesta medio de /ask (últimas 100 consultas)"
            },
            {
                "name": "Aciertos de caché",
                "value": f"{get_cache_hit_rate()}%",
                "raw": get_cache_hit_rate(),
                "status": "ok" if get_cache_hit_rate() > 30 else "warn" if get_cache_hit_rate() > 10 else "info",
                "description": "Porcentaje de respuestas servidas desde caché semántica"
            },
            {
                "name": "Total consultas",
                "value": str(_total_requests),
                "raw": _total_requests,
                "status": "info",
                "description": "Número total de peticiones recibidas en esta sesión"
            },
            {
                "name": "Entidades del grafo",
                "value": str(neo4j["nodes"]),
                "raw": neo4j["nodes"],
                "status": "ok" if neo4j["status"] == "connected" else "error",
                "description": "Entidades almacenadas en el grafo de conocimiento"
            },
            {
                "name": "Relaciones del grafo",
                "value": str(neo4j["relationships"]),
                "raw": neo4j["relationships"],
                "status": "ok" if neo4j["status"] == "connected" else "error",
                "description": "Conexiones entre entidades en el grafo"
            },
            {
                "name": "Fragmentos indexados",
                "value": str(chroma["chunks"]),
                "raw": chroma["chunks"],
                "status": "ok" if chroma["status"] == "connected" else "error",
                "description": "Fragmentos de texto indexados para búsqueda vectorial"
            },
            {
                "name": "Modelo en uso",
                "value": model_display,
                "raw": model_display,
                "status": "info",
                "description": "Modelo de lenguaje principal en uso"
            },
            {
                "name": "Complemento GDS",
                "value": "Activo" if neo4j["gds_available"] else "No disponible",
                "raw": neo4j["gds_available"],
                "status": "ok" if neo4j["gds_available"] else "warn",
                "description": "Graph Data Science plugin de Neo4j (necesario para PageRank)"
            },
            {
                "name": "Tiempo en marcha",
                "value": _format_uptime(uptime_s),
                "raw": uptime_s,
                "status": "ok",
                "description": "Tiempo de actividad del servidor desde el último reinicio"
            },
            {
                "name": "Neo4j",
                "value": neo4j["status"].capitalize(),
                "raw": neo4j["status"],
                "status": "ok" if neo4j["status"] == "connected" else "error",
                "description": "Estado de la conexión con Neo4j"
            },
            {
                "name": "ChromaDB",
                "value": chroma["status"].capitalize(),
                "raw": chroma["status"],
                "status": "ok" if chroma["status"] == "connected" else "error",
                "description": "Estado de la conexión con ChromaDB"
            },
        ]
    }
