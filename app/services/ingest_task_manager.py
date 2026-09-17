"""
Gestor de Tareas de Ingesta Asíncrona en Segundo Plano.
Permite procesar documentos masivos (PDF, TXT, MD) en segundo plano sin bloquear
el hilo HTTP de FastAPI ni provocar timeouts en el frontend.
"""

import uuid
import asyncio
import logging
from typing import Dict, Any, Optional
from datetime import datetime

from app.core.auth import DEFAULT_TENANT_ID

logger = logging.getLogger(__name__)


class IngestTask:
    def __init__(self, task_id: str, filename: str, file_size: int):
        self.task_id = task_id
        self.filename = filename
        self.file_size = file_size
        self.status = "QUEUED"  # QUEUED, EXTRACTING, CHUNKING, GRAPH_EXTRACTION, INDEXING, COMPLETED, FAILED
        self.progress_pct = 5
        self.step_description = "En cola de procesamiento..."
        self.chunks_indexed = 0
        self.entities_ingested = 0
        self.error_message: Optional[str] = None
        self.created_at = datetime.utcnow().isoformat()
        self.completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "filename": self.filename,
            "file_size": self.file_size,
            "status": self.status,
            "progress_pct": self.progress_pct,
            "step_description": self.step_description,
            "chunks_indexed": self.chunks_indexed,
            "entities_ingested": self.entities_ingested,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "completed_at": self.completed_at
        }


class IngestTaskManager:
    def __init__(self):
        self._tasks: Dict[str, IngestTask] = {}

    def create_task(self, filename: str, file_size: int) -> IngestTask:
        task_id = str(uuid.uuid4())
        task = IngestTask(task_id=task_id, filename=filename, file_size=file_size)
        self._tasks[task_id] = task
        logger.info(f"Tarea de ingesta creada: {task_id} para archivo '{filename}' ({file_size} bytes).")
        return task

    def get_task(self, task_id: str) -> Optional[IngestTask]:
        return self._tasks.get(task_id)

    def clear_all(self):
        """Limpia todo el historial de tareas en segundo plano."""
        self._tasks.clear()
        logger.info("IngestTaskManager: Historial de tareas de ingesta vaciado.")

    async def execute_ingest_pipeline(self, task_id: str, raw_bytes: bytes, tenant_id: str = DEFAULT_TENANT_ID):
        """
        Ejecuta el pipeline completo de ingesta en segundo plano actualizando el progreso.
        """
        task = self._tasks.get(task_id)
        if not task:
            logger.error(f"Tarea {task_id} no encontrada para ejecución.")
            return

        try:
            # 1. Extracción de texto con DocumentExtractor multimodelo
            task.status = "EXTRACTING"
            task.progress_pct = 15
            task.step_description = "Extrayendo texto del documento..."
            await asyncio.sleep(0.05)

            from app.core.document_extractor import document_extractor
            extracted = await asyncio.to_thread(document_extractor.extract_from_bytes, task.filename, raw_bytes)
            text = extracted.text

            if not text.strip():
                raise ValueError(f"El documento '{task.filename}' no contiene texto legible (posible escaneo sin OCR o formato no compatible).")

            logger.info(
                f"Extracción exitosa para '{task.filename}': {len(text)} caracteres, "
                f"{extracted.page_count} páginas/secciones, motor: '{extracted.engine_used}'."
            )

            # 2. Fragmentación Estructurada
            task.status = "CHUNKING"
            task.progress_pct = 35
            task.step_description = f"Generando fragmentos semánticos ({extracted.page_count} págs)..."
            await asyncio.sleep(0.05)

            from app.services.knowledge_base import process_file_content
            task.status = "GRAPH_EXTRACTION"
            task.progress_pct = 55
            task.step_description = "Extrayendo entidades y relaciones para el Grafo con IA..."
            await asyncio.sleep(0.05)

            res = await process_file_content(task.filename, text, tenant_id=tenant_id)

            if not res.get("success", False):
                raise RuntimeError(res.get("error", "Error desconocido en process_file_content."))

            task.chunks_indexed = res.get("chunks_indexed", 0)
            task.entities_ingested = res.get("entities_ingested", 0)

            # 3. Sincronización en caliente de índices
            task.status = "INDEXING"
            task.progress_pct = 90
            task.step_description = "Actualizando índice léxico BM25 y sincronizando enrutador..."

            try:
                from app.core.query_router import query_router
                await asyncio.to_thread(query_router.invalidate_and_sync, tenant_id)
            except Exception as e:
                logger.warning(f"Error sincronizando QueryRouter tras ingesta: {e}")

            try:
                from app.db.cache_manager import cache_manager
                cache_manager.invalidate_all(tenant_id=tenant_id)
            except Exception as e:
                logger.warning(f"Error invalidando cache_manager tras ingesta: {e}")

            task.status = "COMPLETED"
            task.progress_pct = 100
            task.step_description = f"✓ Listo ({task.entities_ingested} entidades, {task.chunks_indexed} fragmentos)"
            if res.get("warning"):
                task.step_description = f"⚠ Indexado con avisos ({task.chunks_indexed} fragmentos): {res['warning']}"
                task.error_message = res["warning"]
            task.completed_at = datetime.utcnow().isoformat()
            logger.info(f"Ingesta en segundo plano COMPLETADA para {task.filename} ({task_id}): {task.entities_ingested} entidades, {task.chunks_indexed} chunks.")

        except Exception as e:
            logger.error(f"Error en pipeline de ingesta en segundo plano ({task_id}): {e}", exc_info=True)
            task.status = "FAILED"
            task.progress_pct = 100
            task.step_description = f"Error: {str(e)}"
            task.error_message = str(e)
            task.completed_at = datetime.utcnow().isoformat()


# Instancia singleton predeterminada
ingest_task_manager = IngestTaskManager()

