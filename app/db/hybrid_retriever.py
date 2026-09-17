import os
import logging
from typing import Dict, Any, List, Optional
import concurrent.futures
import chromadb

from app.core.auth import DEFAULT_TENANT_ID
from app.db.graph_manager import graph_manager
from app.core.bm25 import OkapiBM25, reciprocal_rank_fusion
from app.core.reranker import cross_encoder_reranker

logger = logging.getLogger(__name__)

CHROMA_PERSIST_DIR = os.getenv("CHROMADB_PERSIST_DIR", "./chroma_db")
DOC_COLLECTION_NAME = "document_store"
DEFAULT_RRF_K = int(os.getenv("RRF_K", "60"))


class HybridRetriever:
    """
    Motor de Recuperación Híbrida de Grado Empresarial (BM25 + ChromaDB Vector + Reciprocal Rank Fusion + Neo4j Graph).
    Combina la precisión léxica de BM25 para nombres propios, códigos e identificadores con la cobertura
    semántica de embeddings y la topología relacional multi-hop de Neo4j.

    Todo el almacenamiento está particionado por 'tenant_id': cada organización tiene sus
    propios fragmentos en ChromaDB y su propio índice BM25, y ninguna consulta puede
    alcanzar los datos de otra.
    """

    def __init__(self, persist_directory: str = CHROMA_PERSIST_DIR):
        self.client = chromadb.PersistentClient(path=persist_directory)
        self.doc_collection = self.client.get_or_create_collection(
            name=DOC_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}
        )
        # Un índice léxico por organización: compartirlo falsearía el IDF y dejaría que los
        # documentos de un inquilino desplazasen a los de otro en el top-k.
        self.bm25_engines: Dict[str, OkapiBM25] = {}
        logger.info(f"HybridRetriever inicializado con BM25 + ChromaDB ('{DOC_COLLECTION_NAME}') + Neo4j.")

    # --- Particionado por organización ---------------------------------------

    @staticmethod
    def _tenant_filter(tenant_id: str, **extra) -> Dict[str, Any]:
        """Cláusula 'where' de ChromaDB acotada a un inquilino."""
        clauses = [{"tenant_id": tenant_id}] + [{k: v} for k, v in extra.items()]
        return clauses[0] if len(clauses) == 1 else {"$and": clauses}

    def _bm25_for(self, tenant_id: str) -> OkapiBM25:
        """Índice BM25 del inquilino, reconstruido desde ChromaDB la primera vez."""
        engine = self.bm25_engines.get(tenant_id)
        if engine is not None:
            return engine

        engine = OkapiBM25(k1=1.5, b=0.75)
        self.bm25_engines[tenant_id] = engine
        try:
            batch_size, offset, total = 5000, 0, 0
            while True:
                data = self.doc_collection.get(
                    where=self._tenant_filter(tenant_id),
                    include=["documents", "metadatas"],
                    limit=batch_size,
                    offset=offset,
                )
                doc_ids = data.get("ids", [])
                if not doc_ids:
                    break
                documents = data.get("documents", []) or []
                metadatas = data.get("metadatas", []) or []
                engine.add_documents([
                    {"doc_id": d, "text": t, "metadata": m or {}}
                    for d, t, m in zip(doc_ids, documents, metadatas)
                ])
                total += len(doc_ids)
                offset += batch_size
                if len(doc_ids) < batch_size:
                    break
            logger.info(f"Índice BM25 de '{tenant_id}' sincronizado con {total} fragmentos.")
        except Exception as e:
            logger.error(f"Error construyendo índice BM25 de '{tenant_id}': {e}")
        return engine

    def reset_collection(self, tenant_id: Optional[str] = None) -> bool:
        """
        Vacía los fragmentos de un inquilino (o de todos, si no se indica) y reinicia su BM25.
        """
        try:
            if tenant_id is None:
                try:
                    self.client.delete_collection(name=DOC_COLLECTION_NAME)
                except Exception as e:
                    logger.warning(f"Aviso al eliminar colección '{DOC_COLLECTION_NAME}': {e}")
                self.doc_collection = self.client.get_or_create_collection(
                    name=DOC_COLLECTION_NAME,
                    metadata={"hnsw:space": "cosine"}
                )
                self.bm25_engines = {}
                logger.info("HybridRetriever: ChromaDB y BM25 restablecidos (todas las organizaciones).")
                return True

            ids = self.doc_collection.get(where=self._tenant_filter(tenant_id)).get("ids", [])
            if ids:
                self.doc_collection.delete(ids=ids)
            self.bm25_engines[tenant_id] = OkapiBM25(k1=1.5, b=0.75)
            logger.info(f"HybridRetriever: organización '{tenant_id}' restablecida ({len(ids)} fragmentos eliminados).")
            return True
        except Exception as e:
            logger.error(f"Error reiniciando HybridRetriever: {e}")
            return False

    def add_document_chunks_batch(self, chunks: List[Dict[str, Any]], tenant_id: str = DEFAULT_TENANT_ID):
        """
        Indexa una lista de fragmentos en ChromaDB en un único lote y actualiza BM25 incrementalmente.
        Ideal para ingesta de documentos y PDFs extensos.
        """
        if not chunks:
            return
        try:
            # El identificador lleva prefijo de organización: dos empresas pueden subir un
            # archivo con el mismo nombre sin pisarse.
            prepared = [
                {
                    "doc_id": f"{tenant_id}::{c['doc_id']}",
                    "text": c["text"],
                    "metadata": {**c.get("metadata", {}), "tenant_id": tenant_id},
                }
                for c in chunks
            ]
            self.doc_collection.upsert(
                ids=[c["doc_id"] for c in prepared],
                documents=[c["text"] for c in prepared],
                metadatas=[c["metadata"] for c in prepared],
            )
            self._bm25_for(tenant_id).add_documents(prepared)
            logger.info(
                f"Lote de {len(prepared)} fragmentos indexado en ChromaDB y BM25 para '{tenant_id}'."
            )
        except Exception as e:
            logger.error(f"Error al indexar lote de fragmentos en HybridRetriever: {e}")

    def search_vector_store(self, query: str, top_k: int = 15, tenant_id: str = DEFAULT_TENANT_ID) -> List[Dict[str, Any]]:
        """
        Búsqueda semántica pura en ChromaDB, acotada a la organización.
        """
        try:
            results = self.doc_collection.query(
                query_texts=[query],
                n_results=top_k,
                where=self._tenant_filter(tenant_id),
                include=["documents", "metadatas", "distances"]
            )
            chunks = []
            if results and results["documents"] and len(results["documents"][0]) > 0:
                for i in range(len(results["documents"][0])):
                    dist = results["distances"][0][i] if results["distances"] else 0.5
                    doc_id = results["ids"][0][i] if results.get("ids") else f"chunk_{i}"
                    chunks.append({
                        "doc_id": doc_id,
                        "text": results["documents"][0][i],
                        "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                        "similarity": round(1.0 - dist, 4)
                    })
            return chunks
        except Exception as e:
            logger.error(f"Error en búsqueda vectorial ChromaDB: {e}")
            return []

    def search_vector_store_multi(self, queries: List[str], top_k: int = 15, tenant_id: str = DEFAULT_TENANT_ID) -> List[Dict[str, Any]]:
        """
        Búsqueda Multi-Query semántica en ChromaDB con deduplicación.
        """
        if not queries:
            return []

        seen_ids = set()
        aggregated_chunks = []

        for q in queries:
            if not q or len(q.strip()) < 2:
                continue
            for c in self.search_vector_store(q, top_k=top_k, tenant_id=tenant_id):
                cid = c.get("doc_id") or c["text"][:100]
                if cid not in seen_ids:
                    seen_ids.add(cid)
                    aggregated_chunks.append(c)

        aggregated_chunks.sort(key=lambda x: x.get("similarity", 0.0), reverse=True)
        return aggregated_chunks[:top_k]

    def search_bm25(self, query: str, top_k: int = 15, tenant_id: str = DEFAULT_TENANT_ID) -> List[Dict[str, Any]]:
        """
        Búsqueda léxica pura con Okapi BM25 sobre el índice de la organización.
        """
        return self._bm25_for(tenant_id).search(query, top_k=top_k)

    def search_hybrid(
        self,
        query: str,
        top_k: int = 5,
        candidate_pool: int = 15,
        rrf_k: int = DEFAULT_RRF_K,
        additional_queries: Optional[List[str]] = None,
        tenant_id: str = DEFAULT_TENANT_ID
    ) -> List[Dict[str, Any]]:
        """
        Ejecuta la recuperación híbrida BM25 + Vector Search fusionada mediante Reciprocal Rank Fusion (RRF).
        Optimizado con ThreadPoolExecutor para paralelismo.
        """
        def fetch_vector():
            if additional_queries:
                all_v_queries = [query] + additional_queries
                return self.search_vector_store_multi(all_v_queries, top_k=candidate_pool, tenant_id=tenant_id)
            return self.search_vector_store(query, top_k=candidate_pool, tenant_id=tenant_id)

        def fetch_bm25():
            bm25_candidates = self.search_bm25(query, top_k=candidate_pool, tenant_id=tenant_id)
            if additional_queries:
                existing_ids = {b["doc_id"] for b in bm25_candidates}
                for extra_q in additional_queries:
                    for b in self.search_bm25(extra_q, top_k=candidate_pool // 2, tenant_id=tenant_id):
                        if b["doc_id"] not in existing_ids:
                            bm25_candidates.append(b)
                            existing_ids.add(b["doc_id"])
            return bm25_candidates

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_vector = executor.submit(fetch_vector)
            future_bm25 = executor.submit(fetch_bm25)

            vector_candidates = future_vector.result()
            bm25_candidates = future_bm25.result()

        # 3. Fusión Recíproca de Rangos (RRF)
        fused_results = reciprocal_rank_fusion(
            vector_results=vector_candidates,
            bm25_results=bm25_candidates,
            k=rrf_k,
            top_k=candidate_pool
        )

        # 4. Re-ranking Cross-Encoder (Fase 3C)
        final_ranked_chunks = cross_encoder_reranker.rerank(
            query=query,
            candidates=fused_results,
            top_k=top_k
        )

        return final_ranked_chunks

    def get_corpus_overview_chunks(self, limit: int = 15, tenant_id: str = DEFAULT_TENANT_ID) -> List[Dict[str, Any]]:
        """
        Retorna fragmentos representativos de los documentos activos de la organización.
        Garantiza que consultas holísticas ("¿Qué tienen en común los documentos?") tengan
        visibilidad transversal del corpus completo.
        """
        try:
            data = self.doc_collection.get(
                where=self._tenant_filter(tenant_id),
                include=["metadatas", "documents"],
            )
            ids = data.get("ids", [])
            if not ids:
                return []
            metadatas = data.get("metadatas", []) or []
            docs_texts = data.get("documents", []) or []

            seen_files = set()
            overview_chunks = []

            for i, doc_id in enumerate(ids):
                meta = metadatas[i] if i < len(metadatas) else {}
                fname = meta.get("filename", "doc")
                if fname not in seen_files:
                    seen_files.add(fname)
                    overview_chunks.append({
                        "doc_id": doc_id,
                        "text": docs_texts[i] if i < len(docs_texts) else "",
                        "metadata": meta,
                        "similarity": 0.88,
                        "origin_channels": ["corpus_overview"]
                    })
                if len(overview_chunks) >= limit:
                    break
            return overview_chunks
        except Exception as e:
            logger.error(f"Error obteniendo corpus overview chunks: {e}")
            return []

    def retrieve_hybrid_context(
        self,
        query: str,
        entities: List[str],
        top_k_vectors: int = 5,
        max_hops: int = 2,
        additional_queries: Optional[List[str]] = None,
        rrf_k: int = DEFAULT_RRF_K,
        is_global_query: bool = False,
        tenant_id: str = DEFAULT_TENANT_ID
    ) -> Dict[str, Any]:
        """
        Ejecuta la recuperación híbrida completa (BM25 + ChromaDB + RRF + Neo4j Multi-Hop).
        """
        logger.info(
            f"Recuperación Híbrida [{tenant_id}]: '{query[:60]}...' "
            f"(top_k={top_k_vectors}, hops={max_hops}, global={is_global_query})"
        )

        # 1 & 2. Ejecutar Híbrido (BM25+Chroma) y Grafo (Neo4j) en paralelo
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            future_hybrid = executor.submit(
                self.search_hybrid,
                query=query,
                top_k=top_k_vectors,
                candidate_pool=15,
                rrf_k=rrf_k,
                additional_queries=additional_queries,
                tenant_id=tenant_id
            )
            future_graph = executor.submit(
                graph_manager.extract_subgraph,
                entities=entities,
                max_hops=max_hops,
                allow_degree_fallback=is_global_query,
                tenant_id=tenant_id
            )

            fused_chunks = future_hybrid.result()
            graph_subgraph = future_graph.result()

        # Si es una consulta global o transversal de corpus, inyectar visión holística
        if is_global_query or len(fused_chunks) == 0:
            overview_chunks = self.get_corpus_overview_chunks(limit=12, tenant_id=tenant_id)
            existing_cids = {c.get("doc_id") for c in fused_chunks}
            for oc in overview_chunks:
                if oc.get("doc_id") not in existing_cids:
                    fused_chunks.append(oc)
                    existing_cids.add(oc.get("doc_id"))

        # 3. Fusión de Fuentes y Trazabilidad
        sources = set()

        # Fuentes de Grafo
        for node in graph_subgraph.get("nodes", []):
            props = node.get("properties", {})
            name = props.get("name", props.get("title", "Nodo"))
            ntype = props.get("type", "Entity")
            sources.add(f"Neo4j:{ntype}:{name}")

        for path in graph_subgraph.get("paths", []):
            p_str = path.get("path_str", "")
            if p_str:
                sources.add(f"Neo4jPath:{p_str}")

        # Fuentes Documentales con etiqueta de canal RRF
        for chunk in fused_chunks:
            meta = chunk.get("metadata", {})
            filename = meta.get("filename") or _filename_from_id(chunk.get("doc_id", ""))
            header = meta.get("header_path", "")
            channels = "+".join(chunk.get("origin_channels", ["hybrid"]))
            source_tag = f"DocRRF[{channels}]:{filename}" + (f"#{header}" if header else "")
            sources.add(source_tag)

        combined_context = {
            "vector_passages": fused_chunks,
            "graph_nodes": graph_subgraph.get("nodes", []),
            "graph_relationships": graph_subgraph.get("relationships", []),
            "graph_paths": graph_subgraph.get("paths", []),
            "unified_sources": list(sources)
        }

        logger.info(
            f"Recuperación finalizada. Candidatos RRF: {len(fused_chunks)}, "
            f"Nodos: {len(graph_subgraph.get('nodes', []))}, Caminos: {len(graph_subgraph.get('paths', []))}, Fuentes: {len(sources)}"
        )

        return combined_context

    def list_indexed_documents(self, tenant_id: str = DEFAULT_TENANT_ID) -> List[Dict[str, Any]]:
        """
        Retorna la lista agregada de documentos de la organización.
        """
        try:
            data = self.doc_collection.get(
                where=self._tenant_filter(tenant_id),
                include=["metadatas", "documents"],
            )
            ids = data.get("ids", [])
            if not ids:
                return []
            metadatas = data.get("metadatas", []) or []
            docs_texts = data.get("documents", []) or []

            docs_map: Dict[str, Dict[str, Any]] = {}
            for i, doc_id in enumerate(ids):
                meta = metadatas[i] if i < len(metadatas) else {}
                doc_text = docs_texts[i] if i < len(docs_texts) else ""

                fn = (meta.get("filename") if meta else None) or _filename_from_id(doc_id)
                ext = fn.rsplit(".", 1)[-1].upper() if "." in fn else "TXT"
                char_c = (meta.get("char_count") if meta else None) or (len(doc_text) if doc_text else 0)

                if fn not in docs_map:
                    docs_map[fn] = {
                        "filename": fn,
                        "file_type": ext,
                        "chunks_count": 0,
                        "char_count": 0,
                        "header_paths": set()
                    }

                docs_map[fn]["chunks_count"] += 1
                docs_map[fn]["char_count"] += char_c
                h_path = meta.get("header_path") if meta else None
                if h_path:
                    docs_map[fn]["header_paths"].add(h_path)

            result = [
                {
                    "filename": item["filename"],
                    "file_type": item["file_type"],
                    "chunks_count": item["chunks_count"],
                    "char_count": item["char_count"],
                    "headers_count": len(item["header_paths"]),
                }
                for item in docs_map.values()
            ]
            result.sort(key=lambda d: d["filename"].lower())
            return result
        except Exception as e:
            logger.error(f"Error listando documentos en ChromaDB: {e}")
            return []

    def delete_document(self, filename: str, tenant_id: str = DEFAULT_TENANT_ID) -> Dict[str, Any]:
        """
        Elimina los fragmentos de un documento de la organización en ChromaDB,
        desvincula sus nodos en Neo4j, purga la caché semántica y actualiza BM25.
        """
        if not filename or not filename.strip():
            return {"success": False, "filename": filename, "message": "Nombre de archivo no especificado."}

        filename = filename.strip()
        base_fn = os.path.basename(filename.replace("\\", "/")).strip()

        try:
            data = self.doc_collection.get(
                where=self._tenant_filter(tenant_id), include=["metadatas"]
            )
            ids = data.get("ids", [])
            metadatas = data.get("metadatas", []) or []

            # La metadata siempre lleva 'filename'; el identificador solo se usa como respaldo
            # para fragmentos indexados por versiones antiguas.
            objetivo = {filename.lower(), base_fn.lower()}
            target_ids = [
                doc_id for doc_id, meta in zip(ids, metadatas)
                if (str((meta or {}).get("filename", "")).strip().lower() in objetivo
                    or os.path.basename(str((meta or {}).get("filename", "")).replace("\\", "/")).strip().lower() in objetivo
                    or _filename_from_id(doc_id).lower() in objetivo)
            ]

            if not target_ids:
                return {
                    "success": False,
                    "filename": filename,
                    "chunks_deleted": 0,
                    "message": f"No se encontraron fragmentos para el documento '{filename}'."
                }

            self.doc_collection.delete(ids=target_ids)
            logger.info(f"Eliminados {len(target_ids)} fragmentos de '{filename}' en ChromaDB [{tenant_id}].")

            entities_deleted = 0
            try:
                entities_deleted = graph_manager.delete_document_entities(filename, tenant_id=tenant_id)
                if entities_deleted == 0 and base_fn != filename:
                    entities_deleted += graph_manager.delete_document_entities(base_fn, tenant_id=tenant_id)
            except Exception as ge:
                logger.warning(f"Aviso al limpiar entidades de Neo4j para '{filename}': {ge}")

            self._bm25_for(tenant_id).remove_documents_by_ids(target_ids)

            try:
                from app.db.cache_manager import cache_manager
                cache_manager.invalidate_all(tenant_id=tenant_id)
            except Exception:
                pass

            try:
                from app.core.query_router import query_router
                query_router.invalidate_and_sync(tenant_id=tenant_id)
            except Exception:
                pass

            return {
                "success": True,
                "filename": filename,
                "chunks_deleted": len(target_ids),
                "entities_deleted": entities_deleted,
                "remaining_chunks": len(self.doc_collection.get(where=self._tenant_filter(tenant_id)).get("ids", [])),
                "message": f"Documento '{filename}' eliminado con éxito ({len(target_ids)} fragmentos eliminados)."
            }
        except Exception as e:
            logger.error(f"Error eliminando documento '{filename}': {e}")
            return {"success": False, "filename": filename, "error": str(e)}


def _filename_from_id(doc_id: str) -> str:
    """'acme::memoria.txt_chunk_3' -> 'memoria.txt'. Respaldo cuando falta la metadata."""
    sin_tenant = doc_id.split("::", 1)[-1] if "::" in doc_id else doc_id
    return sin_tenant.split("_chunk_")[0] if "_chunk_" in sin_tenant else (sin_tenant or "documento_desconocido")


# Instancia singleton predeterminada
hybrid_retriever = HybridRetriever()
