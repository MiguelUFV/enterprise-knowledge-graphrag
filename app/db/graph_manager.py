import os
import re
import logging
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from neo4j import GraphDatabase, Driver

from app.core.auth import DEFAULT_TENANT_ID
from app.core.secrets import get_neo4j_credentials

# Cargar variables de entorno siempre al importar el módulo
load_dotenv()

logger = logging.getLogger(__name__)

# Claves internas del nodo que el LLM no puede sobrescribir vía 'properties'
RESERVED_ENTITY_KEYS = {"name", "type", "source_doc", "source_docs", "updated_at", "canonical_id", "canonical_name"}


def _clean_properties(props: Any) -> Dict[str, Any]:
    """
    Deja solo propiedades admisibles por Neo4j (primitivos o listas de primitivos)
    y descarta las claves internas para que la extracción LLM no pueda reescribirlas.
    """
    if not isinstance(props, dict):
        return {}
    clean: Dict[str, Any] = {}
    for key, value in props.items():
        if not isinstance(key, str) or key.lower() in RESERVED_ENTITY_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)):
            clean[key] = value
        elif isinstance(value, list) and all(isinstance(v, (str, int, float, bool)) for v in value):
            clean[key] = value
        elif value is not None:
            # dicts anidados u otros tipos: Neo4j los rechaza, se serializan a texto
            clean[key] = str(value)
    return clean


UNKNOWN_ORIGIN = "(origen desconocido)"


def _prov_on_create(var: str) -> str:
    """Nodo nuevo: su único origen es el documento actual."""
    return f"{var}.source_docs = CASE WHEN $source_doc IS NULL THEN [] ELSE [$source_doc] END"


def _prov_on_match(var: str) -> str:
    """
    Nodo que YA existía: se añade el documento actual sin perder los anteriores.
    Si no tenía atribución (nodos antiguos o creados como destino de una relación),
    se marca '(origen desconocido)' para que al borrar el documento nuevo no se
    elimine un nodo que venía de antes.
    """
    return f"""{var}.source_docs = CASE
                WHEN $source_doc IS NULL THEN coalesce({var}.source_docs, [])
                WHEN {var}.source_docs IS NOT NULL AND $source_doc IN {var}.source_docs THEN {var}.source_docs
                WHEN {var}.source_docs IS NOT NULL THEN {var}.source_docs + [$source_doc]
                WHEN {var}.source_doc IS NOT NULL AND {var}.source_doc <> $source_doc THEN [{var}.source_doc, $source_doc]
                WHEN {var}.source_doc IS NOT NULL THEN [$source_doc]
                ELSE ['{UNKNOWN_ORIGIN}', $source_doc]
            END"""


class GraphManager:
    """
    Gestor de conexión y consultas para Neo4j Graph Database.
    Garantiza consultas parametrizadas anti-inyección Cypher y gestión de sesión segura.
    """

    def __init__(self):
        creds = get_neo4j_credentials()  # En producción exige contraseña segura
        self.uri = creds["uri"]
        self.username = creds["username"]
        self.password = creds["password"]
        self._driver: Optional[Driver] = None

    def connect(self) -> Driver:
        """
        Inicializa y retorna el driver de Neo4j de forma segura.
        Las credenciales se leen al instanciar GraphManager (no en cada llamada).
        """
        if not self._driver:
            driver = None
            try:
                driver = GraphDatabase.driver(self.uri, auth=(self.username, self.password))
                driver.verify_connectivity()
            except Exception as e:
                # El driver solo se guarda si la conexión se verificó. Guardarlo antes
                # dejaba uno roto en caché: las llamadas siguientes lo devolvían sin
                # verificar, dejaban de avisar del fallo y no se recuperaban nunca
                # aunque Neo4j volviera.
                if driver is not None:
                    try:
                        driver.close()
                    except Exception:
                        pass
                logger.error(f"Fallo al conectar con la base de datos Neo4j: {str(e)}")
                raise ConnectionError(f"No se pudo conectar a Neo4j en {self.uri}: {str(e)}") from e

            self._driver = driver
            logger.info(f"Conexión exitosa a Neo4j en '{self.uri}'")
            self._ensure_indexes()
        return self._driver

    def _ensure_indexes(self):
        """
        Índice compuesto (tenant_id, name): toda consulta filtra por organización, así que
        sin él cada búsqueda recorrería el grafo entero de todos los inquilinos.
        Es idempotente y no aborta el arranque si la instancia no permite crear índices.
        """
        try:
            with self._driver.session() as session:
                session.run(
                    "CREATE INDEX entity_tenant_name IF NOT EXISTS "
                    "FOR (e:Entity) ON (e.tenant_id, e.name)"
                )
        except Exception as e:
            logger.warning(f"No se pudo crear el índice (tenant_id, name) en Neo4j: {e}")

    def close(self):
        """
        Cierra el driver de conexión a Neo4j.
        """
        if self._driver:
            self._driver.close()
            self._driver = None
            logger.info("Conexión con Neo4j cerrada.")

    def extract_subgraph(
        self,
        entities: List[str],
        max_hops: int = 2,
        max_paths: int = 15,
        allow_degree_fallback: bool = False,
        tenant_id: str = DEFAULT_TENANT_ID
    ) -> Dict[str, Any]:
        """
        Extrae un subgrafo enriquecido y caminos relacionales (Multi-Hop Traversal)
        alrededor de las entidades proporcionadas.

        Estrategia de Traversal Controlado:
        1. Identificación de nodos semilla que coinciden con los términos de búsqueda.
        2. Si hay >= 2 entidades semilla, busca los caminos más cortos (shortestPath) entre pares.
        3. Expansión contextual de caminos hasta 'max_hops' (evitando loops y explosión combinatoria).
        4. Extracción estructurada de nodos, relaciones y cadenas de caminos ('paths').

        :param entities: Lista de nombres, IDs o palabras clave extraídas de la consulta.
        :param max_hops: Profundidad máxima de saltos (default 2, configurable hasta 4).
        :param max_paths: Límite de caminos relevantes a retornar.
        :return: Diccionario con seed_entities, nodes, relationships y paths estructurados.
        """
        driver = self.connect()

        # Configurar límites seguros de saltos (evitar > 4 para no bloquear DBMS)
        safe_hops = max(1, min(int(max_hops), 4))

        # Expandir entidades compuestas a tokens individuales (>3 caracteres) para maximizar recall.
        # La unificación de variantes se hace en la ingesta contra el grafo real (entity_quality),
        # no contra un catálogo fijo: aquí solo se buscan los nombres tal y como existen.
        expanded_entities = set()
        for entity in entities:
            if entity and len(entity.strip()) >= 2:
                expanded_entities.add(entity.strip())
                for word in entity.split():
                    clean_word = re.sub(r'[^a-zA-Z0-9áéíóúÁÉÍÓÚñÑ]', '', word)
                    if len(clean_word) > 3:
                        expanded_entities.add(clean_word)
        search_terms = list(expanded_entities)

        if not search_terms and not allow_degree_fallback:
            logger.info("Sin términos de búsqueda para Neo4j y sin panorámica solicitada: se omite el contexto de grafo.")
            return {"seed_entities": [], "nodes": [], "relationships": [], "paths": []}

        if not search_terms:
            logger.info("Sin términos de búsqueda para Neo4j. Ejecutando extracción de nodos y caminos centrales del grafo.")
            fallback_query = """
            MATCH (n:Entity {tenant_id: $tenant_id})
            OPTIONAL MATCH (n)-[r]->(m:Entity {tenant_id: $tenant_id})
            WITH n, r, m
            LIMIT 40
            RETURN 
                collect(DISTINCT {
                    id: elementId(n),
                    labels: labels(n),
                    properties: properties(n)
                }) AS nodes,
                collect(DISTINCT (CASE WHEN r IS NOT NULL THEN {
                    id: elementId(r),
                    type: type(r),
                    source: startNode(r).name,
                    target: endNode(r).name,
                    properties: properties(r)
                } ELSE null END)) AS relationships
            """
            fallback_paths_query = """
            MATCH (a:Entity {tenant_id: $tenant_id})-[r]->(b:Entity {tenant_id: $tenant_id})
            WHERE a <> b
            RETURN '(' + coalesce(a.name, 'Nodo') + ') -[:' + type(r) + ']-> (' + coalesce(b.name, 'Nodo') + ')' AS path_str,
                   [coalesce(a.name, 'Nodo'), coalesce(b.name, 'Nodo')] AS node_names,
                   [type(r)] AS relationships,
                   1 AS hop_count
            LIMIT 20
            """
            try:
                with driver.session() as session:
                    res = session.run(fallback_query, tenant_id=tenant_id).single()
                    clean_nodes = [n for n in (res["nodes"] or []) if n]
                    clean_rels = [r for r in (res["relationships"] or []) if r]
                    paths_res = session.run(fallback_paths_query, tenant_id=tenant_id).data()
                    return {
                        "seed_entities": [],
                        "nodes": clean_nodes,
                        "relationships": clean_rels,
                        "paths": paths_res or []
                    }
            except Exception as e:
                logger.error(f"Error en consulta fallback de Neo4j: {e}")
                return {"seed_entities": [], "nodes": [], "relationships": [], "paths": []}

        try:
            with driver.session() as session:
                # 1. Localizar nodos semilla
                seed_query = """
                MATCH (n:Entity {tenant_id: $tenant_id})
                WHERE any(e IN $terms WHERE
                    toLower(coalesce(n.name, '')) CONTAINS toLower(e) OR 
                    toLower(e) CONTAINS toLower(coalesce(n.name, '')) OR
                    toLower(coalesce(n.type, '')) CONTAINS toLower(e)
                )
                RETURN elementId(n) AS id, n.name AS name, coalesce(n.type, n.entity_type, 'Entity') AS type, properties(n) AS properties
                LIMIT 20
                """
                seed_records = session.run(seed_query, terms=search_terms, tenant_id=tenant_id).data()
                seed_ids = [r["id"] for r in seed_records]

                if not seed_ids and allow_degree_fallback:
                    # Solo en consultas globales: usar los nodos más conectados como panorámica.
                    # En consultas concretas sería ruido que infla el prompt y puede despistar al LLM.
                    fallback_records = session.run("""
                    MATCH (n:Entity {tenant_id: $tenant_id})
                    OPTIONAL MATCH (n)-[r]->(:Entity {tenant_id: $tenant_id})
                    WITH n, count(r) AS degree
                    ORDER BY degree DESC
                    LIMIT 15
                    RETURN elementId(n) AS id, n.name AS name, coalesce(n.type, n.entity_type, 'Entity') AS type, properties(n) AS properties
                    """, tenant_id=tenant_id).data()
                    seed_records = fallback_records
                    seed_ids = [r["id"] for r in seed_records]

                if not seed_ids:
                    logger.info(f"Ninguna entidad de la consulta existe en el grafo ({search_terms[:5]}): sin contexto de grafo.")
                    return {"seed_entities": search_terms, "nodes": [], "relationships": [], "paths": []}

                if not seed_ids:
                    return {
                        "seed_entities": search_terms,
                        "nodes": [],
                        "relationships": [],
                        "paths": []
                    }

                found_paths: List[Dict[str, Any]] = []
                collected_nodes: Dict[str, Dict[str, Any]] = {}
                collected_rels: List[Dict[str, Any]] = []

                # Registrar nodos semilla
                for s in seed_records:
                    collected_nodes[s["name"]] = {
                        "id": s["id"],
                        "name": s["name"],
                        "type": s["type"],
                        "properties": s["properties"]
                    }

                # 2. Si hay >= 2 nodos semilla, buscar shortestPath entre pares
                if len(seed_ids) >= 2:
                    for i in range(min(len(seed_ids), 4)):
                        for j in range(i + 1, min(len(seed_ids), 4)):
                            sp_query = f"""
                            // a y b salen de seed_ids, ya acotado a la organización
                            MATCH (a:Entity) WHERE elementId(a) = $idA
                            MATCH (b:Entity) WHERE elementId(b) = $idB
                            MATCH p = shortestPath((a)-[*..{safe_hops}]-(b))
                            WHERE all(n IN nodes(p) WHERE n.tenant_id = $tenant_id)
                            RETURN [n in nodes(p) | {{name: n.name, type: coalesce(n.type, n.entity_type, 'Entity'), properties: properties(n)}}] as nodes,
                                   [r in relationships(p) | {{type: type(r), source: startNode(r).name, target: endNode(r).name, properties: properties(r)}}] as rels,
                                   length(p) as hops
                            LIMIT 3
                            """
                            sp_results = session.run(
                                sp_query, idA=seed_ids[i], idB=seed_ids[j], tenant_id=tenant_id
                            ).data()
                            for r in sp_results:
                                found_paths.append(r)

                # 3. Expansión contextual multi-hop desde los nodos semilla
                exp_query = f"""
                // a sale de seed_ids, ya acotado a la organización; el camino entero se valida abajo
                MATCH (a:Entity)
                WHERE elementId(a) IN $seed_ids
                MATCH p = (a)-[r*1..{safe_hops}]-(b:Entity {{tenant_id: $tenant_id}})
                WHERE b <> a AND all(n IN nodes(p) WHERE n.tenant_id = $tenant_id)
                WITH p, length(p) AS hops
                ORDER BY hops ASC
                LIMIT 25
                RETURN [n in nodes(p) | {{name: n.name, type: coalesce(n.type, n.entity_type, 'Entity'), properties: properties(n)}}] AS nodes,
                       [r in relationships(p) | {{type: type(r), source: startNode(r).name, target: endNode(r).name, properties: properties(r)}}] AS rels,
                       hops
                """
                exp_results = session.run(exp_query, seed_ids=seed_ids, tenant_id=tenant_id).data()
                found_paths.extend(exp_results)

                # 4. Deduplicar y formatear caminos estructurados
                seen_signatures = set()
                formatted_paths: List[Dict[str, Any]] = []

                for p in found_paths:
                    p_nodes = p.get("nodes", [])
                    p_rels = p.get("rels", [])
                    hops = p.get("hops", len(p_rels))

                    node_names = [n["name"] for n in p_nodes if n and "name" in n]
                    rel_types = [r["type"] for r in p_rels if r and "type" in r]

                    sig = " -> ".join(node_names) + " | " + "-".join(rel_types)
                    if sig in seen_signatures or len(node_names) < 2:
                        continue

                    # Hardening de ALIAS_OF y prevención de ciclos
                    if len(node_names) != len(set(node_names)):
                        continue  # Descartar caminos cíclicos (A -> B -> A)
                    if rel_types.count("ALIAS_OF") > 1:
                        continue  # Restringir ALIAS_OF a 1 salto para evitar deriva transitiva A -> B -> C

                    seen_signatures.add(sig)

                    # Registrar nodos y relaciones únicos
                    for n in p_nodes:
                        if n.get("name") and n["name"] not in collected_nodes:
                            collected_nodes[n["name"]] = n
                    for r in p_rels:
                        collected_rels.append(r)

                    # Crear representación legible del camino: (A) -[:REL]-> (B)
                    path_elements = []
                    for k, n_name in enumerate(node_names):
                        path_elements.append(f"({n_name})")
                        if k < len(rel_types):
                            path_elements.append(f"-[:{rel_types[k]}]->")
                    path_str = " ".join(path_elements)

                    formatted_paths.append({
                        "path_str": path_str,
                        "node_names": node_names,
                        "relationships": rel_types,
                        "hop_count": hops
                    })

                    if len(formatted_paths) >= max_paths:
                        break

                logger.info(
                    f"Multi-Hop Traversal completado para {search_terms}: "
                    f"{len(collected_nodes)} nodos, {len(formatted_paths)} caminos estructurados (hops={safe_hops})."
                )

                # Convertir nodos a formato estándar
                nodes_list = [
                    {
                        "id": v.get("id", v.get("name")),
                        "properties": {
                            "name": v.get("name"),
                            "type": v.get("type", "Entity"),
                            **(v.get("properties") or {})
                        }
                    }
                    for v in collected_nodes.values()
                ]

                return {
                    "seed_entities": search_terms,
                    "nodes": nodes_list,
                    "relationships": collected_rels,
                    "paths": formatted_paths,
                    "max_hops": safe_hops
                }

        except Exception as e:
            logger.error(f"Error en Multi-Hop Traversal de Neo4j: {str(e)}")
            return {
                "seed_entities": search_terms,
                "nodes": [],
                "relationships": [],
                "paths": [],
                "error": str(e)
            }

    def upsert_entity_and_relations(
        self, payload: Dict[str, Any], source_doc: Optional[str] = None,
        tenant_id: str = DEFAULT_TENANT_ID
    ) -> Dict[str, Any]:
        """
        Realiza un MERGE seguro de la entidad y sus relaciones en Neo4j.
        Previene inyección Cypher parametrizando todos los datos e inyectando solo etiquetas sanitizadas.

        :param payload: Diccionario con entity_name, entity_type, properties y relaciones.
        :param source_doc: Nombre del documento origen (opcional) para rastreo y borrado selectivo.
        :return: Resultado del procesamiento con el ID del nodo y el conteo de relaciones.
        """
        entity_name = payload["entity_name"]
        entity_type = payload.get("entity_type", "Entity")
        properties = _clean_properties(payload.get("properties", {}))
        relations = payload.get("relations", [])
        src = source_doc or payload.get("source_doc")

        driver = self.connect()

        # La identidad del nodo es (organización, nombre): dos empresas pueden tener una
        # entidad llamada igual sin que MERGE las funda en una sola.
        main_query = f"""
        MERGE (e:Entity {{tenant_id: $tenant_id, name: $entity_name}})
        ON CREATE SET {_prov_on_create('e')}
        ON MATCH SET {_prov_on_match('e')}
        SET e.type = $entity_type,
            e.updated_at = timestamp(),
            e += $properties,
            e.source_doc = coalesce(e.source_doc, $source_doc)
        RETURN elementId(e) as node_id
        """

        try:
            with driver.session() as session:
                res = session.run(
                    main_query,
                    parameters={
                        "entity_name": entity_name,
                        "entity_type": entity_type,
                        "properties": properties,
                        "source_doc": src,
                        "tenant_id": tenant_id
                    }
                )
                record = res.single()
                node_id = record["node_id"] if record else "unknown"

                relations_processed = 0
                for rel in relations:
                    target_name = rel.get("target_name") if isinstance(rel, dict) else rel.target_name
                    target_type = rel.get("target_type", "Entity") if isinstance(rel, dict) else getattr(rel, "target_type", "Entity")
                    raw_rel_type = rel.get("relation_type", "RELATED_TO") if isinstance(rel, dict) else getattr(rel, "relation_type", "RELATED_TO")
                    rel_props = rel.get("properties", {}) if isinstance(rel, dict) else getattr(rel, "properties", {})

                    clean_rel_type = re.sub(r'[^a-zA-Z0-9_]', '_', str(raw_rel_type)).upper()
                    if not clean_rel_type or clean_rel_type[0].isdigit():
                        clean_rel_type = "RELATED_TO"

                    rel_query = f"""
                    MATCH (e:Entity {{tenant_id: $tenant_id, name: $entity_name}})
                    MERGE (target:Entity {{tenant_id: $tenant_id, name: $target_name}})
                    ON CREATE SET {_prov_on_create('target')}
                    ON MATCH SET {_prov_on_match('target')}
                    SET target.type = coalesce(target.type, $target_type),
                        target.source_doc = coalesce(target.source_doc, $source_doc)
                    MERGE (e)-[r:{clean_rel_type}]->(target)
                    ON CREATE SET {_prov_on_create('r')}
                    ON MATCH SET {_prov_on_match('r')}
                    SET r += $rel_props
                    RETURN elementId(r) as rel_id
                    """

                    session.run(
                        rel_query,
                        parameters={
                            "entity_name": entity_name,
                            "target_name": target_name,
                            "target_type": target_type,
                            "rel_props": _clean_properties(rel_props),
                            "source_doc": src,
                            "tenant_id": tenant_id
                        }
                    )
                    relations_processed += 1

                logger.info(f"Ingesta exitosa en Neo4j: Entidad '{entity_name}' procesada con {relations_processed} relaciones.")
                return {
                    "success": True,
                    "node_id": node_id,
                    "relations_processed": relations_processed
                }

        except Exception as e:
            logger.error(f"Error al realizar MERGE en Neo4j para entidad '{entity_name}': {str(e)}")
            raise RuntimeError(f"Error en ingesta de datos a Neo4j: {str(e)}") from e

    def upsert_entities_batch(
        self, entities: List[Dict[str, Any]], source_doc: Optional[str] = None,
        tenant_id: str = DEFAULT_TENANT_ID
    ) -> int:
        """
        Ingesta un lote completo de entidades y relaciones en Neo4j usando transacciones Cypher optimizadas con UNWIND.
        Reduce de decenas de peticiones de red a solo 1-2 transacciones de alto rendimiento.
        """
        if not entities:
            return 0

        clean_entities = []
        clean_relations = []
        seen_names = set()

        for ent in entities:
            name = ent.get("entity_name") if isinstance(ent, dict) else getattr(ent, "entity_name", None)
            if not name or name.lower() in seen_names:
                continue
            seen_names.add(name.lower())

            etype = ent.get("entity_type", "Entity") if isinstance(ent, dict) else getattr(ent, "entity_type", "Entity")
            props = ent.get("properties", {}) if isinstance(ent, dict) else getattr(ent, "properties", {})

            clean_entities.append({
                "name": name,
                "type": etype,
                "properties": _clean_properties(props)
            })

            rels = ent.get("relations", []) if isinstance(ent, dict) else getattr(ent, "relations", [])
            for r in rels:
                target_name = r.get("target_name") if isinstance(r, dict) else getattr(r, "target_name", None)
                if not target_name:
                    continue
                target_type = r.get("target_type", "Entity") if isinstance(r, dict) else getattr(r, "target_type", "Entity")
                raw_rel_type = r.get("relation_type", "RELATED_TO") if isinstance(r, dict) else getattr(r, "relation_type", "RELATED_TO")
                rel_props = r.get("properties", {}) if isinstance(r, dict) else getattr(r, "properties", {})

                clean_rel_type = re.sub(r'[^a-zA-Z0-9_]', '_', str(raw_rel_type)).upper()
                if not clean_rel_type or clean_rel_type[0].isdigit():
                    clean_rel_type = "RELATED_TO"

                clean_relations.append({
                    "source_name": name,
                    "target_name": target_name,
                    "target_type": target_type,
                    "relation_type": clean_rel_type,
                    "properties": _clean_properties(rel_props)
                })

        driver = self.connect()
        try:
            with driver.session() as session:
                # 1. Ingestar todos los nodos de forma masiva con UNWIND
                node_query = f"""
                UNWIND $entities AS ent
                MERGE (e:Entity {{tenant_id: $tenant_id, name: ent.name}})
                ON CREATE SET {_prov_on_create('e')}
                ON MATCH SET {_prov_on_match('e')}
                SET e.type = ent.type,
                    e.updated_at = timestamp(),
                    e += ent.properties,
                    e.source_doc = coalesce(e.source_doc, $source_doc)
                """
                session.run(node_query, parameters={
                    "entities": clean_entities, "source_doc": source_doc, "tenant_id": tenant_id
                })

                # 2. Agrupar y crear relaciones por tipo de relación
                rels_by_type: Dict[str, List[Dict[str, Any]]] = {}
                for rel in clean_relations:
                    rtype = rel["relation_type"]
                    if rtype not in rels_by_type:
                        rels_by_type[rtype] = []
                    rels_by_type[rtype].append(rel)

                for rtype, rlist in rels_by_type.items():
                    rel_query = f"""
                    UNWIND $rels AS r
                    MATCH (e:Entity {{tenant_id: $tenant_id, name: r.source_name}})
                    MERGE (target:Entity {{tenant_id: $tenant_id, name: r.target_name}})
                    ON CREATE SET {_prov_on_create('target')}
                    ON MATCH SET {_prov_on_match('target')}
                    SET target.type = coalesce(target.type, r.target_type),
                        target.source_doc = coalesce(target.source_doc, $source_doc)
                    MERGE (e)-[rel:{rtype}]->(target)
                    ON CREATE SET {_prov_on_create('rel')}
                    ON MATCH SET {_prov_on_match('rel')}
                    SET rel += r.properties
                    """
                    session.run(rel_query, parameters={
                        "rels": rlist, "source_doc": source_doc, "tenant_id": tenant_id
                    })

            logger.info(f"Ingesta masiva en Neo4j: {len(clean_entities)} entidades y {len(clean_relations)} relaciones insertadas ultra-rápido.")
            return len(clean_entities)
        except Exception as e:
            logger.error(f"Error en ingesta masiva por lote en Neo4j: {e}")
            # Fallback a inserción secuencial si falla la consulta masiva
            success_count = 0
            for ent in entities:
                try:
                    self.upsert_entity_and_relations(ent, source_doc=source_doc, tenant_id=tenant_id)
                    success_count += 1
                except Exception:
                    pass
            return success_count

    def delete_document_entities(self, filename: str, tenant_id: str = DEFAULT_TENANT_ID) -> int:
        """
        Elimina o desvincula nodos en Neo4j asociados al documento especificado.
        Soporta nombres completos, basenames, rutas relativas y normalización de mayúsculas/minúsculas.
        """
        if not filename or not filename.strip():
            return 0

        filename = filename.strip()
        basename = os.path.basename(filename).strip()
        targets = list({filename.lower(), basename.lower()})
        driver = self.connect()

        # 'remaining' = documentos de origen que sobreviven al borrado.
        # Se calcula una sola vez (WITH) para que borrar y desvincular usen el mismo criterio.
        match_clause = """
        MATCH (e:Entity {tenant_id: $tenant_id})
        WHERE toLower(coalesce(e.source_doc, '')) IN $targets
           OR any(d IN coalesce(e.source_docs, []) WHERE toLower(d) IN $targets)
        WITH e, [d IN coalesce(e.source_docs, []) WHERE NOT toLower(d) IN $targets] AS remaining
        """

        try:
            with driver.session() as session:
                # 1. Eliminar solo las entidades que no pertenecen a ningún otro documento
                res = session.run(match_clause + """
                WHERE size(remaining) = 0
                DETACH DELETE e
                RETURN count(*) AS deleted
                """, targets=targets, tenant_id=tenant_id).single()
                del_count = res["deleted"] if res else 0

                # 2. Desvincular el documento de las entidades compartidas, conservándolas
                upd = session.run(match_clause + """
                WHERE size(remaining) > 0
                SET e.source_docs = remaining,
                    e.source_doc = CASE
                        WHEN toLower(coalesce(e.source_doc, '')) IN $targets THEN remaining[0]
                        ELSE e.source_doc
                    END
                RETURN count(*) AS updated
                """, targets=targets, tenant_id=tenant_id).single()

                # 3. Igual para las relaciones: solo se borran las que no aporta ningún otro documento
                rel_match = """
                MATCH (:Entity {tenant_id: $tenant_id})-[r]->(:Entity {tenant_id: $tenant_id})
                WHERE any(d IN coalesce(r.source_docs, []) WHERE toLower(d) IN $targets)
                WITH r, [d IN coalesce(r.source_docs, []) WHERE NOT toLower(d) IN $targets] AS remaining
                """
                rel_del = session.run(rel_match + """
                WHERE size(remaining) = 0
                DELETE r
                RETURN count(*) AS deleted
                """, targets=targets, tenant_id=tenant_id).single()
                session.run(rel_match + """
                WHERE size(remaining) > 0
                SET r.source_docs = remaining
                """, targets=targets, tenant_id=tenant_id)

                logger.info(
                    f"Neo4j para '{filename}': {del_count} entidades eliminadas, "
                    f"{upd['updated'] if upd else 0} desvinculadas, "
                    f"{rel_del['deleted'] if rel_del else 0} relaciones eliminadas."
                )
                return del_count
        except Exception as e:
            logger.warning(f"Aviso al eliminar entidades del documento '{filename}' en Neo4j: {e}")
            return 0


    def get_entity_names(self, limit: int = 10000, tenant_id: str = DEFAULT_TENANT_ID) -> List[str]:
        """Nombres de las entidades ya presentes en el grafo (para unificar variantes en la ingesta)."""
        try:
            with self.connect().session() as session:
                records = session.run(
                    "MATCH (n:Entity {tenant_id: $tenant_id}) WHERE n.name IS NOT NULL "
                    "RETURN n.name AS name LIMIT $limit",
                    limit=limit, tenant_id=tenant_id
                ).data()
            return [r["name"] for r in records if r.get("name")]
        except Exception as e:
            logger.warning(f"No se pudieron leer los nombres de entidades del grafo: {e}")
            return []

    def get_graph_stats(self, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
        """
        Retorna estadísticas del grafo de la organización: nodos y relaciones.
        Propaga los errores de conexión para que el panel de métricas muestre el fallo.
        """
        driver = self.connect()
        with driver.session() as session:
            node_count = session.run(
                "MATCH (n:Entity {tenant_id: $tenant_id}) RETURN count(n) AS c", tenant_id=tenant_id
            ).single()["c"]
            rel_count = session.run(
                "MATCH (:Entity {tenant_id: $tenant_id})-[r]->(:Entity {tenant_id: $tenant_id}) "
                "RETURN count(r) AS c", tenant_id=tenant_id
            ).single()["c"]
            return {"nodes": node_count, "relationships": rel_count}

    _gds_cache: Optional[bool] = None
    _gds_cache_ts: float = 0.0
    _GDS_CACHE_TTL: float = 300.0  # 5 minutos de TTL

    def check_gds_available(self) -> bool:
        """
        Detecta si el plugin Graph Data Science está instalado en Neo4j.
        Resultado cacheado por 5 minutos para evitar consultas repetitivas.
        """
        import time
        now = time.time()
        if GraphManager._gds_cache is not None and (now - GraphManager._gds_cache_ts) < GraphManager._GDS_CACHE_TTL:
            return GraphManager._gds_cache

        try:
            driver = self.connect()
            with driver.session() as session:
                result = session.run("CALL gds.version() YIELD version RETURN version")
                version = result.single()
                if version:
                    logger.info(f"GDS detectado: versión {version['version']}")
                    GraphManager._gds_cache = True
                    GraphManager._gds_cache_ts = now
                    return True
                GraphManager._gds_cache = False
                GraphManager._gds_cache_ts = now
                return False
        except Exception:
            logger.info("GDS no disponible en esta instancia de Neo4j.")
            GraphManager._gds_cache = False
            GraphManager._gds_cache_ts = now
            return False

    def get_full_graph_for_viz(self, max_nodes: int = 500, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
        """
        Extrae los nodos y relaciones del grafo de la organización, limitado a max_nodes nodos.
        Optimizado para exportación al visor 3D.
        """
        driver = self.connect()
        query = """
        MATCH (n:Entity {tenant_id: $tenant_id})
        WITH n LIMIT $max_nodes
        OPTIONAL MATCH (n)-[r]->(m:Entity {tenant_id: $tenant_id})
        WHERE m IS NOT NULL
        RETURN
            collect(DISTINCT {
                id: elementId(n),
                labels: labels(n),
                properties: properties(n)
            }) AS nodes,
            collect(DISTINCT (CASE WHEN r IS NOT NULL THEN {
                id: elementId(r),
                type: type(r),
                source: elementId(startNode(r)),
                target: elementId(endNode(r)),
                properties: properties(r)
            } ELSE null END)) AS relationships
        """
        try:
            with driver.session() as session:
                res = session.run(query, parameters={"max_nodes": max_nodes, "tenant_id": tenant_id}).single()
                if not res:
                    return {"nodes": [], "relationships": []}
                nodes = [n for n in (res["nodes"] or []) if n]
                rels  = [r for r in (res["relationships"] or []) if r]
                return {"nodes": nodes, "relationships": rels}
        except Exception as e:
            logger.error(f"Error extrayendo grafo para visualización: {e}")
            return {"nodes": [], "relationships": [], "error": str(e)}

    def get_pagerank_scores(self, max_nodes: int = 500, tenant_id: str = DEFAULT_TENANT_ID) -> dict:
        """
        Calcula PageRank usando el plugin GDS y retorna un diccionario
        {node_element_id: pagerank_score}.
        Solo llamar si check_gds_available() retorna True.
        """
        driver = self.connect()
        query = """
        CALL gds.pageRank.stream({
            nodeQuery: 'MATCH (n:Entity) WHERE n.tenant_id = $tenant_id RETURN id(n) AS id',
            relationshipQuery: 'MATCH (s:Entity)-[r]->(t:Entity) WHERE s.tenant_id = $tenant_id AND t.tenant_id = $tenant_id RETURN id(s) AS source, id(t) AS target',
            parameters: {tenant_id: $tenant_id},
            maxIterations: 20,
            dampingFactor: 0.85
        })
        YIELD nodeId, score
        RETURN elementId(gds.util.asNode(nodeId)) AS elementId, score
        LIMIT $max_nodes
        """
        try:
            with driver.session() as session:
                results = session.run(query, parameters={"max_nodes": max_nodes, "tenant_id": tenant_id})
                return {record["elementId"]: record["score"] for record in results}
        except Exception as e:
            logger.error(f"Error calculando PageRank con GDS: {e}")
            return {}

# Instancia singleton predeterminada
graph_manager = GraphManager()
