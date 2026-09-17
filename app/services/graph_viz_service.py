"""
Servicio de exportación del grafo para visualización 3D.
Devuelve nodos y aristas en formato compatible con 3d-force-graph.
Detecta automáticamente si GDS está disponible para añadir métricas de centralidad.
"""
import asyncio
import logging
from typing import Dict, Any, List

from app.core.auth import DEFAULT_TENANT_ID

logger = logging.getLogger(__name__)

# Color por tipo de entidad. Pigmentos apagados sobre el lienzo grafito del visor:
# se distinguen entre sí sin convertir el grafo en un semáforo. Cubre el vocabulario
# completo que produce la extracción (ver CANONICAL_TYPES en app/core/entity_quality.py);
# un tipo sin entrada aquí se dibujaría todo del mismo color y el grafo perdería lectura.
LABEL_COLORS = {
    "Person":        "#d9b382",  # arena
    "Organization":  "#4d9e84",  # pino, el acento de la marca
    "Product":       "#7fa8c9",  # azul pálido
    "Service":       "#9bb36a",  # musgo
    "Project":       "#c98d6b",  # arcilla
    "Technology":    "#6fa5a0",  # verde azulado
    "Policy":        "#a58fb0",  # malva
    "Contract":      "#c9a227",  # ocre
    "Incident":      "#c4736b",  # teja
    "Location":      "#8fa8b8",  # pizarra azulada
    "Document":      "#b9bdb6",  # papel
    "Role":          "#b59b7c",  # lino
    "Event":         "#a89bb8",  # lavanda apagada
    "Certification": "#7fb39b",  # verde claro
    "Program":       "#9aa98c",  # salvia
    "Plan":          "#9aa98c",  # salvia
    "Entity":        "#8a929b",  # sin tipo reconocido
}
DEFAULT_COLOR = "#8a929b"


def _get_node_color(labels: List[str], properties: dict = None) -> str:
    """
    Color del nodo según su tipo de entidad.

    Todos los nodos llevan la etiqueta :Entity en Neo4j; el tipo real (Person,
    Contract, Incident...) vive en la propiedad 'type'. Mirar solo las etiquetas
    pintaba el grafo entero de un mismo color.
    """
    tipo = (properties or {}).get("type") or (properties or {}).get("entity_type")
    if tipo and tipo in LABEL_COLORS:
        return LABEL_COLORS[tipo]
    for label in labels:
        if label in LABEL_COLORS and label != "Entity":
            return LABEL_COLORS[label]
    return DEFAULT_COLOR


def _get_display_name(properties: dict) -> str:
    """Extrae el nombre más representativo de las propiedades del nodo."""
    for key in ("name", "nombre", "title", "id", "type"):
        val = properties.get(key)
        if val and isinstance(val, str) and len(val) > 0:
            return val[:40]
    return "Nodo"


async def get_graph_data_for_viz(max_nodes: int = 500, tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
    """
    Extrae el grafo completo de Neo4j y lo transforma al formato
    esperado por 3d-force-graph: {nodes: [...], links: [...]}.

    Añade métricas de centralidad si GDS está disponible.
    Usa grado de conexión como fallback si GDS no está instalado.

    :param max_nodes: Límite máximo de nodos a incluir.
    :return: Diccionario con nodos y enlaces.
    """
    from app.db.graph_manager import graph_manager

    try:
        raw = await asyncio.to_thread(graph_manager.get_full_graph_for_viz, max_nodes, tenant_id)
    except Exception as e:
        logger.error(f"Error obteniendo datos del grafo: {e}")
        return {"nodes": [], "links": [], "error": "No se pudo obtener el grafo."}

    raw_nodes = raw.get("nodes", [])
    raw_rels = raw.get("relationships", [])

    if not raw_nodes:
        return {"nodes": [], "links": [], "node_count": 0, "link_count": 0}

    # Detectar GDS y obtener centralidad si está disponible
    gds_available = await asyncio.to_thread(graph_manager.check_gds_available)
    pagerank_scores: Dict[str, float] = {}

    if gds_available:
        try:
            pagerank_scores = await asyncio.to_thread(graph_manager.get_pagerank_scores, max_nodes, tenant_id)
            logger.info(f"PageRank calculado para {len(pagerank_scores)} nodos.")
        except Exception as e:
            logger.warning(f"PageRank falló aunque GDS reportó disponible: {e}")
            gds_available = False

    # Calcular grado de cada nodo como fallback de importancia
    degree_map: Dict[str, int] = {}
    for rel in raw_rels:
        if rel:
            src = rel.get("source", "")
            tgt = rel.get("target", "")
            degree_map[src] = degree_map.get(src, 0) + 1
            degree_map[tgt] = degree_map.get(tgt, 0) + 1

    # Normalizar importancia a rango [1, 10] para tamaño visual
    if pagerank_scores:
        max_pr = max(pagerank_scores.values()) if pagerank_scores else 1.0
        min_pr = min(pagerank_scores.values()) if pagerank_scores else 0.0
        pr_range = max_pr - min_pr if max_pr != min_pr else 1.0

    max_degree = max(degree_map.values()) if degree_map else 1

    # Construir lista de nodos formateada
    nodes_out = []
    node_ids = set()

    for node in raw_nodes:
        if not node:
            continue
        node_id = node.get("id", "")
        if not node_id or node_id in node_ids:
            continue
        node_ids.add(node_id)

        labels = node.get("labels", []) or []
        props = node.get("properties", {}) or {}
        name = _get_display_name(props)
        color = _get_node_color(labels, props)

        # Calcular tamaño visual
        if gds_available and node_id in pagerank_scores:
            pr = pagerank_scores[node_id]
            normalized = (pr - min_pr) / pr_range
            size = 3 + normalized * 12  # rango: 3–15
        else:
            deg = degree_map.get(node_id, 0)
            size = 3 + (deg / max_degree) * 10

        nodes_out.append({
            "id": node_id,
            "name": name,
            "labels": labels,
            "color": color,
            "size": round(size, 2),
            "properties": {k: str(v)[:100] for k, v in props.items() if v is not None},
        })

    # Construir lista de enlaces
    links_out = []
    for rel in raw_rels:
        if not rel:
            continue
        src = rel.get("source", "")
        tgt = rel.get("target", "")
        if src in node_ids and tgt in node_ids:
            links_out.append({
                "source": src,
                "target": tgt,
                "type": rel.get("type", "RELATED_TO"),
                "properties": rel.get("properties", {})
            })

    logger.info(
        f"Grafo 3D preparado: {len(nodes_out)} nodos, {len(links_out)} enlaces. "
        f"GDS={gds_available}"
    )

    return {
        "nodes": nodes_out,
        "links": links_out,
        "node_count": len(nodes_out),
        "link_count": len(links_out),
        "gds_used": gds_available,
    }
