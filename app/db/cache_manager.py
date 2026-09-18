import os
import json
import logging
import re
import uuid
from typing import Optional, Dict, Any, List
import chromadb

from app.core.auth import DEFAULT_TENANT_ID

logger = logging.getLogger(__name__)

# Directorio de persistencia local para ChromaDB
CHROMA_PERSIST_DIR = os.getenv("CHROMADB_PERSIST_DIR", "./chroma_db")
COLLECTION_NAME = "semantic_cache"
SIMILARITY_THRESHOLD = 0.92  # Umbral de similitud mínima (0.92)

# Cualquier palabra que contenga una cifra: códigos de contrato (CNT-2021-BCP-001),
# de incidencia (INC-2026-001), versiones, años, porcentajes, importes.
_DISCRIMINANTE = re.compile(r"[\w][\w\-./,%:]*\d[\w\-./,%:]*", re.UNICODE)


def _identificadores(texto: str) -> frozenset:
    """
    Los datos que distinguen una pregunta de otra casi idéntica.

    Dos preguntas que solo se diferencian en un código son casi el mismo vector: la
    similitud coseno entre "¿presupuesto del proyecto NEXUS-RESEARCH-001?" y la misma
    con -009 supera el 0.92 con holgura. Sin esta comprobación la caché respondía a la
    segunda con la respuesta de la primera, y encima la etiquetaba como evidencia
    suficiente: la invención exacta que el resto del sistema existe para evitar.
    """
    return frozenset(m.group(0).lower().strip(".,;:") for m in _DISCRIMINANTE.finditer(texto or ""))


class CacheManager:
    """
    Gestor de Caché Semántica utilizando ChromaDB local.
    Permite almacenar y recuperar respuestas basadas en la similitud coseno de los embeddings.
    """

    def __init__(self, persist_directory: str = CHROMA_PERSIST_DIR):
        self.client = chromadb.PersistentClient(path=persist_directory)
        # Crear o recuperar colección con espacio métrico Coseno
        self.collection = self.client.get_or_create_collection(
            name=COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"}
        )
        logger.info(f"ChromaDB CacheManager inicializado en '{persist_directory}' (Colección: {COLLECTION_NAME})")

    def get_cached_answer(
        self,
        query: str,
        threshold: float = SIMILARITY_THRESHOLD,
        tenant_id: str = DEFAULT_TENANT_ID
    ) -> Optional[Dict[str, Any]]:
        """
        Busca una respuesta previamente almacenada en la caché semántica de la organización.

        El filtro por 'tenant_id' es obligatorio: dos empresas distintas hacen preguntas
        casi idénticas ("¿quién es el director financiero?") y sin él una recibiría la
        respuesta construida con los documentos de la otra.

        :param query: Pregunta del usuario.
        :param threshold: Umbral de similitud coseno (por defecto 0.92).
        :return: Diccionario con los datos de la respuesta en caché si supera el umbral, o None si no hay coincidencia.
        """
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=1,
                where={"tenant_id": tenant_id},
                include=["metadatas", "distances", "documents"]
            )

            if not results or not results["distances"] or len(results["distances"][0]) == 0:
                return None

            distance = results["distances"][0][0]
            # Para la métrica Coseno en ChromaDB: similitud = 1 - distancia
            similarity = 1.0 - distance

            logger.info(f"Caché Semántica lookup - Distancia: {distance:.4f}, Similitud calculada: {similarity:.4f} (Umbral: {threshold})")

            if similarity >= threshold:
                metadata = results["metadatas"][0][0]

                # La similitud no basta: tiene que preguntar por lo mismo, no por algo
                # que se le parece. Si los códigos o las cifras no coinciden, es un fallo
                # de caché aunque los vectores estén pegados.
                guardada = metadata.get("original_query", "")
                if _identificadores(query) != _identificadores(guardada):
                    logger.info(
                        "Caché descartada pese a la similitud: los identificadores no coinciden "
                        f"({sorted(_identificadores(query))} vs {sorted(_identificadores(guardada))})."
                    )
                    return None

                sources = json.loads(metadata.get("sources", "[]"))
                return {
                    "text": metadata.get("response_text", ""),
                    "sources": sources,
                    "confidence": float(metadata.get("confidence", 0.0)),
                    "uncertainty_level": metadata.get("uncertainty_level"),
                    "route": metadata.get("route"),
                    "similarity": similarity,
                    "origin": "cache"
                }

            return None

        except Exception as e:
            logger.error(f"Error al consultar la caché semántica en ChromaDB: {str(e)}")
            return None

    def set_cached_answer(
        self,
        query: str,
        response_text: str,
        sources: List[str],
        confidence: float,
        uncertainty_level: str,
        route: str,
        tenant_id: str = DEFAULT_TENANT_ID
    ) -> bool:
        """
        Guarda una consulta y su respuesta en la caché semántica de ChromaDB,
        conservando el nivel de evidencia y la ruta para que la UI no los falsee al servir desde caché.
        """
        try:
            # La clave incluye la organización: la misma pregunta de dos empresas son dos entradas.
            doc_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{tenant_id}|{query}"))
            metadata = {
                "response_text": response_text,
                "sources": json.dumps(sources),
                "confidence": confidence,
                "uncertainty_level": uncertainty_level,
                "route": route,
                "original_query": query,
                "tenant_id": tenant_id
            }

            self.collection.upsert(
                ids=[doc_id],
                documents=[query],
                metadatas=[metadata]
            )
            logger.info(f"Respuesta guardada con éxito en caché semántica (ID: {doc_id})")
            return True

        except Exception as e:
            logger.error(f"Error al guardar respuesta en caché semántica ChromaDB: {str(e)}")
            return False

    def invalidate_all(self, tenant_id: Optional[str] = None) -> bool:
        """
        Vacía la caché semántica de una organización, o la de todas si no se indica ninguna.
        """
        try:
            if tenant_id is not None:
                ids = self.collection.get(where={"tenant_id": tenant_id}).get("ids", [])
                if ids:
                    self.collection.delete(ids=ids)
                logger.info(f"Caché semántica de '{tenant_id}' invalidada ({len(ids)} entradas).")
                return True

            self.client.delete_collection(name=COLLECTION_NAME)
            self.collection = self.client.get_or_create_collection(
                name=COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"}
            )
            logger.info(f"Caché semántica ('{COLLECTION_NAME}') invalidada y reinicializada con éxito.")
            return True
        except Exception as e:
            logger.error(f"Error al invalidar la caché semántica: {e}")
            return False


# Instancia singleton predeterminada
cache_manager = CacheManager()
