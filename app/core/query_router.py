"""
Módulo de Enrutamiento Adaptativo de Consultas (QueryRouter).
Implementa clasificación determinista de complejidad (Fast Path, Hybrid Path, Full Graph RAG),
verificación de suficiencia de evidencia y escalación automática sin añadir llamadas LLM adicionales.
"""
import re
import logging
from typing import Dict, Any, List, Tuple, Optional

from app.core.auth import DEFAULT_TENANT_ID

logger = logging.getLogger(__name__)

# Expresiones regulares para identificadores corporativos exactos
EXACT_PATTERNS = [
    r'\bCNT-\d{4}-[A-Z]{3}-\d{3}\b',      # Contratos: CNT-2023-MRI-008
    r'\bINC-\d{4}-\d{3}\b',              # Incidencias: INC-2026-001
    r'\bEP\d{7}\b',                      # Patentes: EP3948201
    r'\bCIF\s+[A-Z]-\d{8}\b',            # CIF legal: CIF B-87654321
    r'\b(ISO-\d{5}(?::\d{4})?)\b',       # Certificaciones ISO
    r'\b(SOC\s+2(?:\s+Type\s+II)?)\b',   # Certificaciones SOC 2
    r'\bENS\b',                          # Esquema Nacional de Seguridad
    r'\bLIC-\d{4}-[A-Z0-9-]+\b',         # Licitaciones públicas
]

# Keywords que denotan complejidad relacional o multi-salto obligatoria
RELATIONAL_KEYWORDS = [
    "relación entre", "relacion entre",
    "cómo se conecta", "como se conecta",
    "a través de", "a traves de",
    "quién depende de", "quien depende de",
    "cadena de mando", "organigrama completo",
    "impacto cruzado", "camino entre", "ruta entre",
    "dependencia jerárquica", "dependencia jerarquica",
    "intervinieron en", "participaron en la resolución",
    "compara los contratos", "comparar"
]

# Keywords de consultas globales de corpus / resumen transversal
GLOBAL_OVERVIEW_KEYWORDS = [
    "en común", "en comun",
    "tienen en común", "tienen en comun",
    "de qué tratan", "de que tratan",
    "de qué habla", "de que habla",
    "resumen de los documentos", "resumen de los apuntes",
    "resumen general", "resumen del corpus", "visión general", "vision general",
    "documentos subidos", "archivos subidos", "apuntes subidos",
    "qué documentos", "que documentos", "qué archivos", "que archivos",
    "qué información hay", "que informacion hay", "qué contenido hay", "que contenido hay",
    "temas principales", "temática principal", "tematica principal",
    "comparativa de documentos", "similitudes entre los documentos"
]

# Patrones interrogativos de atributo directo (Factuales simples)
FACTUAL_ATTRIBUTE_PATTERNS = [
    r'\bcu[aá]l es el precio\b',
    r'\bcu[aá]nto cuesta\b',
    r'\bcu[aá]l es el sla\b',
    r'\bcu[aá]ntas consultas\b',
    r'\bd[oó]nde est[aá] la sede\b',
    r'\bqui[eé]n es el ceo\b',
    r'\bcu[aá]l es el cif\b',
    r'\bqu[eé] capital social\b',
    r'\bqu[eé] penalizaci[oó]n\b',
    r'\bqu[eé] valor anual\b',
    r'\bcu[aá]l es la duraci[oó]n\b'
]



class QueryRouter:
    """
    Enrutador determinista que clasifica la complejidad de la consulta
    y supervisa el camino adaptativo de ejecución.
    Se auto-alimenta dinámicamente de los nodos del Grafo en Neo4j.
    """

    def __init__(
        self,
        min_fast_cosine_sim: float = 0.72,
        min_fast_dominance_gap: float = 1.35,
        min_fast_bm25_score: float = 2.5
    ):
        self.min_fast_cosine_sim = min_fast_cosine_sim
        self.min_fast_dominance_gap = min_fast_dominance_gap
        self.min_fast_bm25_score = min_fast_bm25_score
        # Un catálogo de entidades por organización: el enrutador de una empresa nunca
        # debe reconocer los nombres propios de otra.
        self.dynamic_entities: Dict[str, List[str]] = {}
        self._synced_tenants: set = set()

    def sync_entities_from_graph(self, tenant_id: str = DEFAULT_TENANT_ID, graph_mgr=None) -> int:
        """
        Carga y sincroniza dinámicamente hasta 10.000 entidades indexadas en Neo4j.
        Permite que el router aprenda al instante los nombres, personas, clientes
        y productos de cualquier empresa nueva sin tocar código.
        """
        try:
            if graph_mgr is None:
                from app.db.graph_manager import graph_manager
                graph_mgr = graph_manager
            driver = graph_mgr.connect()
            query = """
            MATCH (n:Entity {tenant_id: $tenant_id})
            WHERE n.name IS NOT NULL AND size(trim(n.name)) >= 2
            RETURN DISTINCT toLower(trim(n.name)) AS name
            ORDER BY size(name) DESC
            LIMIT 10000
            """
            with driver.session() as session:
                records = session.run(query, tenant_id=tenant_id).data()
                names = [r["name"] for r in records if r.get("name")]
                if names:
                    self.dynamic_entities[tenant_id] = names
                    self._synced_tenants.add(tenant_id)
                    logger.info(
                        f"QueryRouter [{tenant_id}]: {len(names)} entidades sincronizadas desde el Grafo (Caché L1)."
                    )
                    return len(names)
        except Exception as e:
            logger.warning(f"QueryRouter: No se pudieron sincronizar entidades desde Neo4j ({e}).")
        return len(self.dynamic_entities.get(tenant_id, []))

    def invalidate_and_sync(self, tenant_id: str = DEFAULT_TENANT_ID) -> int:
        """
        Invalida la caché del enrutador y fuerza una re-sincronización en caliente.
        Ideal para invocarse tras una ingesta de nuevos documentos.
        """
        self._synced_tenants.discard(tenant_id)
        return self.sync_entities_from_graph(tenant_id=tenant_id)

    def get_known_entities(self, tenant_id: str = DEFAULT_TENANT_ID) -> List[str]:
        """
        Entidades conocidas, siempre tomadas del grafo de esa organización.
        Sin documentos ingeridos la lista está vacía: el enrutador cae a las señales
        sintácticas y al lookup L2, nunca a nombres precargados.
        """
        if tenant_id not in self._synced_tenants or not self.dynamic_entities.get(tenant_id):
            self.sync_entities_from_graph(tenant_id=tenant_id)
        return self.dynamic_entities.get(tenant_id, [])

    def lookup_dynamic_entities_in_graph(self, query: str, tenant_id: str = DEFAULT_TENANT_ID) -> List[str]:
        """
        Búsqueda dinámica bajo demanda en Neo4j (Caché L2 / On-Demand Graph Lookup).
        Permite detectar entidades en grafos con más de 100.000 nodos sin sobrecargar
        la memoria del servidor con expresiones regulares gigantes.
        """
        try:
            # Extraer n-gramas significativos de 3 o más caracteres (omitiendo stopwords comunes)
            stopwords = {"para", "como", "cual", "donde", "cuando", "quien", "sobre", "entre", "este", "esta", "estos", "estas", "cual", "cuales", "precio", "cuanto", "cuesta"}
            words = [w for w in re.findall(r'\b[a-zA-Z0-9áéíóúÁÉÍÓÚñÑüÜ]{3,}\b', query.lower()) if w not in stopwords]
            if not words:
                return []

            from app.db.graph_manager import graph_manager
            driver = graph_manager.connect()
            cypher = """
            MATCH (n:Entity {tenant_id: $tenant_id})
            WHERE any(w IN $words WHERE toLower(coalesce(n.name, '')) CONTAINS w)
            RETURN DISTINCT toLower(trim(n.name)) AS name
            LIMIT 15
            """
            with driver.session() as session:
                records = session.run(cypher, words=words, tenant_id=tenant_id).data()
                return [r["name"] for r in records if r.get("name")]
        except Exception as e:
            logger.debug(f"Lookup dinámico de entidades en grafo omitido: {e}")
            return []

    def classify_pre_retrieval(self, query: str, tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
        """
        Determina la ruta preliminar (FAST_PATH, HYBRID_PATH, FULL_GRAPH_RAG)
        usando señales sintácticas, regex, caché en memoria y búsqueda bajo demanda en grafo.
        """
        q_lower = query.lower().strip()

        # 1. Extraer identificadores alfanuméricos exactos
        detected_ids = []
        for pattern in EXACT_PATTERNS:
            matches = re.findall(pattern, query, flags=re.IGNORECASE)
            for m in matches:
                val = m if isinstance(m, str) else m[0]
                if val and val not in detected_ids:
                    detected_ids.append(val)

        # 2. Detectar señales relacionales explícitas
        detected_rel_signals = [
            kw for kw in RELATIONAL_KEYWORDS if kw in q_lower
        ]

        # 3. Detectar entidades conocidas mencionadas (Caché L1 en memoria)
        active_entities = self.get_known_entities(tenant_id=tenant_id)
        raw_entities = [
            ent for ent in active_entities if ent in q_lower
        ]

        # 3b. Si no se encontraron suficientes entidades en memoria, consultar L2 (Grafo Neo4j)
        if len(raw_entities) == 0:
            l2_entities = self.lookup_dynamic_entities_in_graph(query, tenant_id=tenant_id)
            for ent in l2_entities:
                if ent in q_lower and ent not in raw_entities:
                    raw_entities.append(ent)

        # Conservar únicamente la entidad más específica/larga si una contiene a la otra
        pruned_entities = []
        for ent in sorted(raw_entities, key=len, reverse=True):
            if not any(ent in other for other in pruned_entities):
                pruned_entities.append(ent)
        detected_entities = pruned_entities

        # 4. Detectar si coincide con patrón factual de atributo directo
        is_direct_factual = any(
            re.search(pat, q_lower) for pat in FACTUAL_ATTRIBUTE_PATTERNS
        )

        # =====================================================================
        # LÓGICA DE DECISIÓN DETERMINISTA (Reglas de prioridad)
        # =====================================================================

        # REGLA 0: Consulta Global / Resumen de Colección / Similitud entre Documentos
        is_global = any(kw in q_lower for kw in GLOBAL_OVERVIEW_KEYWORDS)
        if is_global:
            return {
                "route": "FULL_GRAPH_RAG",
                "is_global_query": True,
                "reason": "Consulta global de resumen, comparación o similitudes entre documentos del corpus.",
                "suggested_hops": 2,
                "needs_reranker": True,
                "needs_graph": True,
                "detected_ids": detected_ids,
                "detected_entities": detected_entities
            }

        # REGLA A: Si hay señales relacionales explícitas O >= 3 entidades distintas
        # -> FULL_GRAPH_RAG obligatorio (Multi-Hop profundo necesario)
        if detected_rel_signals or len(detected_entities) >= 3:
            return {
                "route": "FULL_GRAPH_RAG",
                "reason": f"Señales relacionales fuertes ({detected_rel_signals}) o >= 3 entidades ({len(detected_entities)}).",
                "suggested_hops": 2 if len(detected_entities) <= 3 else 3,
                "needs_reranker": True,
                "needs_graph": True,
                "detected_ids": detected_ids,
                "detected_entities": detected_entities
            }

        # REGLA B: Identificador exacto presente Y sin señales de multi-entidad
        # -> FAST_PATH (BM25 tiene máxima precisión aquí)
        if detected_ids and len(detected_entities) <= 1 and not detected_rel_signals:
            return {
                "route": "FAST_PATH",
                "reason": f"Identificador alfanumérico exacto detectado: {detected_ids}.",
                "suggested_hops": 0,
                "needs_reranker": False,
                "needs_graph": False,
                "detected_ids": detected_ids,
                "detected_entities": detected_entities
            }

        # REGLA C: Atributo factual monotemático con a lo sumo 1 entidad
        # -> FAST_PATH
        if is_direct_factual and len(detected_entities) <= 1 and not detected_rel_signals:
            return {
                "route": "FAST_PATH",
                "reason": "Pregunta factual directa sobre atributo de una sola entidad.",
                "suggested_hops": 0,
                "needs_reranker": False,
                "needs_graph": False,
                "detected_ids": detected_ids,
                "detected_entities": detected_entities
            }

        # REGLA D: Exactamente 2 entidades conectadas sin multi-hop profundo
        # -> HYBRID_PATH (1 hop relacional suficiente)
        if len(detected_entities) == 2:
            return {
                "route": "HYBRID_PATH",
                "reason": f"Dos entidades detectadas ({detected_entities}): requiere confirmación relacional de 1 salto.",
                "suggested_hops": 1,
                "needs_reranker": True,
                "needs_graph": True,
                "detected_ids": detected_ids,
                "detected_entities": detected_entities
            }

        # REGLA E: Por defecto seguro -> HYBRID_PATH
        return {
            "route": "HYBRID_PATH",
            "reason": "Consulta estándar con incertidumbre relacional: enrutamiento a Hybrid Path por precaución.",
            "suggested_hops": 1,
            "needs_reranker": True,
            "needs_graph": True,
            "detected_ids": detected_ids,
            "detected_entities": detected_entities
        }

    def verify_fast_path_sufficiency(
        self,
        query: str,
        bm25_results: List[Dict[str, Any]],
        vector_results: List[Dict[str, Any]],
        detected_ids: Optional[List[str]] = None
    ) -> Tuple[bool, str, List[Dict[str, Any]]]:
        """
        Post-Retrieval Guard: Evalúa si los resultados recuperados en FAST_PATH
        ofrecen evidencia suficiente, clara e inequívoca para responder directamente.
        Si la evidencia es débil o ambigua, escala automáticamente a HYBRID_PATH.
        """
        if not bm25_results and not vector_results:
            return False, "Sin candidatos de recuperación en Fast Path.", []

        top_bm25 = bm25_results[0] if bm25_results else None
        top_vec = vector_results[0] if vector_results else None

        # 1. Caso de Oro: Identificador exacto presente en el texto del Top 1 de BM25
        if detected_ids and top_bm25:
            top_text = top_bm25.get("text", "")
            for did in detected_ids:
                if did.lower() in top_text.lower():
                    logger.info(f"Fast Path confirmado: Identificador '{did}' presente en Top 1 BM25.")
                    # Fusionar con vector si existe
                    passages = [top_bm25]
                    if top_vec and top_vec.get("doc_id") != top_bm25.get("doc_id"):
                        passages.append(top_vec)
                    return True, f"Identificador '{did}' confirmado en evidencia Top-1.", passages

        # 2. Comprobar similitud vectorial y dominancia de BM25
        top_vec_sim = top_vec.get("similarity", 0.0) if top_vec else 0.0
        top_bm25_score = top_bm25.get("bm25_score", 0.0) if top_bm25 else 0.0

        # Calcular gap de dominancia en BM25 si hay al menos 2 candidatos
        if len(bm25_results) >= 2:
            second_bm25 = bm25_results[1].get("bm25_score", 0.001)
            dominance_gap = top_bm25_score / max(second_bm25, 0.001)
        else:
            dominance_gap = 2.0

        # Consenso: El Top 1 de BM25 coincide con el Top 1 de Vector
        has_consensus = (
            top_bm25 is not None and top_vec is not None and
            top_bm25.get("doc_id") == top_vec.get("doc_id")
        )

        # Criterio de Aprobación Fast Path:
        # A) Consenso directo y similitud sólida
        if has_consensus and top_vec_sim >= 0.70:
            return True, "Consenso Top-1 entre BM25 y Vector Search con similitud sólida.", [top_vec]

        # B) Similitud vectorial alta y margen de dominancia claro
        if top_vec_sim >= self.min_fast_cosine_sim and dominance_gap >= self.min_fast_dominance_gap:
            return True, f"Evidencia vectorial dominante (Sim={top_vec_sim:.2f}, Gap={dominance_gap:.2f}).", [top_vec]

        # C) BM25 extraordinariamente contundente (> 4.5)
        if top_bm25_score >= 4.5 and dominance_gap >= 1.5:
            return True, f"Evidencia léxica concluyente (BM25={top_bm25_score:.2f}, Gap={dominance_gap:.2f}).", [top_bm25]

        # De lo contrario, la evidencia es ambigua o parcial -> ESCALACIÓN OBLIGATORIA
        logger.info(
            f"Fast Path insuficiente (VecSim={top_vec_sim:.2f}, BM25={top_bm25_score:.2f}, Gap={dominance_gap:.2f}). "
            f"Escalando a HYBRID_PATH."
        )
        return False, "Evidencia ambigua o umbrales insuficientes: escalando a Hybrid Path.", []


# Instancia singleton del router
query_router = QueryRouter()
