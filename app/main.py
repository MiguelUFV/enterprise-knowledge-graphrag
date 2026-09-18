import os
import glob
import time
import asyncio
import logging
import secrets
from typing import Optional
from dotenv import load_dotenv
load_dotenv()
from fastapi import FastAPI, Request, HTTPException, Header, UploadFile, File, Query, status, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from app.core.schemas import QueryRequest, RAGResponse, IngestPayload
from app.core.guardrails import check_input_safety, is_obviously_malicious, UnsafeQueryError
from app.core.security_audit import security_auditor
from app.core.auth import (
    DEFAULT_TENANT_ID,
    normalize_tenant_id,
    get_current_user,
    require_role,
    create_access_token,
    Role,
    TokenPayload,
    TokenResponse,
    ACCESS_TOKEN_EXPIRE_MINUTES
)
from app.core.secrets import get_webhook_api_key, IS_PRODUCTION
from app.services.ingest_task_manager import ingest_task_manager
from app.agent.graph_agent import run_agent_flow
from app.core.tenancy import tenant_display_name
from app.db.cache_manager import cache_manager, SIMILARITY_THRESHOLD as CACHE_SIMILARITY_THRESHOLD
from app.db.graph_manager import graph_manager
from app.db.hybrid_retriever import hybrid_retriever

logger = logging.getLogger(__name__)

limiter = Limiter(key_func=get_remote_address)

# Token estático para Webhooks de Ingesta (Make / n8n / CRM). Obligatorio en producción.
WEBHOOK_API_KEY = get_webhook_api_key()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ALLOWED_DOC_EXTENSIONS = ("pdf", "docx", "txt", "md", "csv", "json")
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024
ALLOWED_AUDIO_EXTENSIONS = ("webm", "wav", "mp3", "m4a", "ogg")
MAX_AUDIO_SIZE_BYTES = 25 * 1024 * 1024

UNSAFE_QUERY_DETAIL = (
    "Entrada rechazada: Se ha detectado una violación de seguridad, lenguaje no permitido "
    "o intento de Prompt Injection."
)

app = FastAPI(
    title="Enterprise Hybrid GraphRAG API",
    description="API del asistente de conocimiento corporativo: recuperación híbrida sobre vectores y grafo.",
    version="3.0.0",
    # /docs describe cada endpoint y su esquema: útil desarrollando, innecesario
    # en producción, donde solo sirve para que un tercero estudie la superficie.
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None,
    openapi_url=None if IS_PRODUCTION else "/openapi.json",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

allowed_origins = os.getenv(
    "ALLOWED_ORIGINS",
    "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-KEY"],
)

static_dir = os.path.join(BASE_DIR, "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")


# La interfaz carga three.js desde unpkg y las tipografías desde Google Fonts.
# Todo lo demás debe venir de este servidor; 'unsafe-inline' en estilos es necesario
# porque el visor del grafo compone estilos en el propio elemento.
CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    "script-src 'self' https://unpkg.com",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

SECURITY_HEADERS = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # El dictado por voz necesita el micrófono; nada más.
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=(), interest-cohort=()",
}


@app.middleware("http")
async def security_and_cache_headers(request: Request, call_next):
    """
    Añade las cabeceras de seguridad y obliga a revalidar la interfaz.

    Sin la revalidación, tras desplegar una versión nueva el navegador sigue sirviendo
    el HTML y el JavaScript viejos de su caché y la aplicación queda a medias. El ETag
    hace que la revalidación devuelva 304 y no cueste ancho de banda si nada cambió.
    """
    response = await call_next(request)

    for cabecera, valor in SECURITY_HEADERS.items():
        response.headers.setdefault(cabecera, valor)

    if IS_PRODUCTION:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )

    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"

    return response


async def _read_upload_limited(file: UploadFile, max_bytes: int) -> bytes:
    """Lee como máximo max_bytes + 1 para rechazar archivos grandes sin cargarlos enteros en memoria."""
    raw = await file.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El archivo excede el tamaño máximo permitido ({max_bytes // (1024*1024)} MB)."
        )
    if not raw:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El archivo proporcionado está vacío.")
    return raw


def _file_extension(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


async def _refresh_indexes_after_change(tenant_id: str):
    """Invalida la caché semántica y resincroniza el enrutador tras cambios en el corpus."""
    from app.core.query_router import query_router
    await asyncio.to_thread(cache_manager.invalidate_all, tenant_id)
    await asyncio.to_thread(query_router.invalidate_and_sync, tenant_id)


# =========================================================================
# Frontend y sistema
# =========================================================================
@app.get("/", include_in_schema=False)
async def serve_frontend():
    return FileResponse(os.path.join(static_dir, "index.html"))


@app.get("/health", tags=["System Health"])
@limiter.limit("30/minute")
async def health_check(request: Request):
    return {"status": "healthy", "service": "Enterprise Hybrid GraphRAG API", "version": "3.0.0"}


class LoginRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=128)
    role: Role = Role.STANDARD
    tenant_id: str = Field(DEFAULT_TENANT_ID, min_length=1, max_length=63)


@app.post("/api/auth/token", response_model=TokenResponse, tags=["Authentication"])
@limiter.limit("20/minute")
async def generate_auth_token(request: Request, login: LoginRequest):
    """
    Emisión de tokens de prueba SOLO en desarrollo: no valida credenciales.
    En producción los tokens deben emitirlos un proveedor de identidad (SSO/OIDC).
    """
    if IS_PRODUCTION:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    tenant_id = normalize_tenant_id(login.tenant_id)
    token = create_access_token(
        user_id=login.user_id, role=login.role.value,
        access_level=login.role.value, tenant_id=tenant_id
    )
    return TokenResponse(
        access_token=token,
        token_type="Bearer",
        user_id=login.user_id,
        role=login.role.value,
        access_level=login.role.value,
        tenant_id=tenant_id,
        expires_in_seconds=ACCESS_TOKEN_EXPIRE_MINUTES * 60
    )


# =========================================================================
# Consulta GraphRAG
# =========================================================================
@app.post("/ask", response_model=RAGResponse, tags=["GraphRAG"])
@limiter.limit("10/minute")
async def ask_question(
    request: Request,
    query_request: QueryRequest,
    current_user: TokenPayload = Depends(get_current_user)
):
    """
    1. Filtro heurístico local anti-injection (0 ms, siempre).
    2. Caché semántica (similitud >= 0.92).
    3. Si no hay acierto de caché: guardrail LLM en paralelo con la recuperación del agente.
       Nada se genera hasta que el guardrail confirma que la consulta es segura.
    4. Sanitización de salida, guardado en caché y registro de latencia.
    """
    from app.services.metrics_service import record_request

    t_start = time.monotonic()

    if is_obviously_malicious(query_request.question):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=UNSAFE_QUERY_DETAIL)

    tenant_id = current_user.tenant_id
    cached_response = await asyncio.to_thread(
        cache_manager.get_cached_answer, query_request.question, CACHE_SIMILARITY_THRESHOLD, tenant_id
    )
    if cached_response:
        record_request(latency_ms=(time.monotonic() - t_start) * 1000, from_cache=True)
        return RAGResponse(
            text=security_auditor.sanitize_output(cached_response["text"]),
            sources=cached_response["sources"],
            confidence=cached_response["confidence"],
            confidence_score=cached_response["confidence"],
            uncertainty_level=cached_response["uncertainty_level"],
            route=cached_response["route"],
            origin="cache"
        )

    # El guardrail LLM se lanza ya y se verifica dentro del agente, justo antes de generar
    safety_gate = asyncio.create_task(check_input_safety(query_request.question))
    try:
        result = await run_agent_flow(
            query_request.question, safety_gate=safety_gate, tenant_id=tenant_id
        )
    except UnsafeQueryError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=UNSAFE_QUERY_DETAIL)
    except RuntimeError as e:
        logger.error(f"Error en el flujo del agente: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="El servicio de IA no está disponible en este momento. Inténtelo de nuevo más tarde."
        )
    finally:
        if not safety_gate.done():
            safety_gate.cancel()

    sanitized_text = security_auditor.sanitize_output(result["text"])
    conf = float(result.get("confidence_score", 0.0))
    uncertainty_level = result.get("uncertainty_level", "LEVEL_D_NO_EVIDENCE")
    route = result.get("route", "HYBRID_PATH")

    await asyncio.to_thread(
        cache_manager.set_cached_answer,
        query_request.question,
        sanitized_text,
        result["sources"],
        conf,
        uncertainty_level,
        route,
        tenant_id
    )

    record_request(latency_ms=(time.monotonic() - t_start) * 1000, from_cache=False)

    return RAGResponse(
        text=sanitized_text,
        sources=result["sources"],
        confidence=conf,
        confidence_score=conf,
        uncertainty_level=uncertainty_level,
        evidence_grounding=result.get("evidence_grounding", {}),
        graph_paths=result.get("graph_paths", []),
        route=route,
        origin="generation"
    )


# =========================================================================
# Ingesta
# =========================================================================
@app.post("/webhook/ingest", tags=["Ingestion"])
async def ingest_payload(
    payload: IngestPayload,
    x_api_key: Optional[str] = Header(None, alias="X-API-KEY")
):
    """Ingesta de entidades vía Webhook (Make, n8n, CRM), protegida con cabecera 'X-API-KEY'."""
    if not x_api_key or not secrets.compare_digest(x_api_key, WEBHOOK_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Acceso denegado: Cabecera 'X-API-KEY' no válida o ausente."
        )

    try:
        # El webhook se autentica con X-API-KEY, no con JWT: ingiere en la organización
        # por defecto del despliegue salvo que el propio payload declare otra.
        tenant_id = normalize_tenant_id(getattr(payload, "tenant_id", None))
        result = await asyncio.to_thread(
            graph_manager.upsert_entity_and_relations, payload.model_dump(), None, tenant_id
        )
    except Exception as e:
        logger.error(f"Fallo en la ingesta vía Webhook: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error interno durante la ingesta de datos."
        )
    return {
        "status": "success",
        "message": f"Entidad '{payload.entity_name}' y sus relaciones han sido ingeridas con éxito en Neo4j.",
        "data": result
    }


@app.post("/api/ingest-document", tags=["Ingestion"])
async def ingest_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: TokenPayload = Depends(require_role(Role.STANDARD))
):
    """Recibe un documento y lanza su ingesta en segundo plano. Devuelve el ID de seguimiento."""
    filename = os.path.basename(file.filename or "uploaded_file.txt")
    ext = _file_extension(filename)

    if ext not in ALLOWED_DOC_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Formato de archivo no soportado: .{ext}. Formatos compatibles: {', '.join('.' + e for e in ALLOWED_DOC_EXTENSIONS)}."
        )

    raw_bytes = await _read_upload_limited(file, MAX_FILE_SIZE_BYTES)

    task = ingest_task_manager.create_task(filename=filename, file_size=len(raw_bytes))
    background_tasks.add_task(
        ingest_task_manager.execute_ingest_pipeline, task.task_id, raw_bytes, current_user.tenant_id
    )

    return {
        "status": "queued",
        "task_id": task.task_id,
        "filename": filename,
        "file_size": len(raw_bytes),
        "message": f"Documento '{filename}' recibido. Procesamiento iniciado en segundo plano."
    }


@app.get("/api/ingest-status/{task_id}", tags=["Ingestion"])
async def get_ingest_task_status(task_id: str, current_user: TokenPayload = Depends(get_current_user)):
    task = ingest_task_manager.get_task(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No se encontró la tarea de ingesta con ID '{task_id}'."
        )
    return task.to_dict()


@app.post("/api/load-samples", tags=["Ingestion"])
async def load_sample_data(current_user: TokenPayload = Depends(require_role(Role.STANDARD))):
    """Ingiere los archivos .txt y .md de sample_docs/ en Neo4j y ChromaDB."""
    from app.services.knowledge_base import process_file_content

    sample_dir = os.path.join(BASE_DIR, "sample_docs")
    files = glob.glob(os.path.join(sample_dir, "*.txt")) + glob.glob(os.path.join(sample_dir, "*.md"))
    if not files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No se encontraron documentos en la carpeta 'sample_docs/'."
        )

    total_entities = total_chunks = processed = 0
    for file_path in files:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        result = await process_file_content(
            os.path.basename(file_path), content, tenant_id=current_user.tenant_id
        )
        if result.get("success"):
            total_entities += result.get("entities_ingested", 0)
            total_chunks += result.get("chunks_indexed", 0)
            processed += 1

    await _refresh_indexes_after_change(current_user.tenant_id)

    return {
        "status": "success",
        "files_processed": processed,
        "total_entities": total_entities,
        "total_chunks": total_chunks
    }


# =========================================================================
# Base de conocimiento
# =========================================================================
@app.get("/api/app-config", tags=["Configuration"])
def get_app_config(current_user: TokenPayload = Depends(get_current_user)):
    """Configuración White-Label y estado del corpus de la organización que consulta."""
    tenant_id = current_user.tenant_id
    try:
        total_chunks = sum(d["chunks_count"] for d in hybrid_retriever.list_indexed_documents(tenant_id))
    except Exception:
        total_chunks = 0

    try:
        total_entities = graph_manager.get_graph_stats(tenant_id=tenant_id)["nodes"]
    except Exception:
        total_entities = 0

    return {
        "company_name": tenant_display_name(tenant_id),
        "app_subtitle": os.getenv("APP_SUBTITLE", "Plataforma de Inteligencia Corporativa con GraphRAG"),
        "tenant_id": tenant_id,
        "total_chunks": total_chunks,
        "total_entities": total_entities,
        "is_empty": (total_chunks == 0 and total_entities == 0)
    }


@app.get("/api/documents", tags=["Knowledge Base"])
def get_indexed_documents(current_user: TokenPayload = Depends(get_current_user)):
    docs = hybrid_retriever.list_indexed_documents(current_user.tenant_id)
    return {
        "status": "success",
        "total_documents": len(docs),
        "total_chunks": sum(d["chunks_count"] for d in docs),
        "documents": docs
    }


class DeleteDocumentRequest(BaseModel):
    filename: Optional[str] = Field(None, description="Nombre del archivo a eliminar")
    all: bool = Field(False, description="Si es True, elimina todos los documentos (requiere rol admin)")


def _delete_single(filename: str, tenant_id: str) -> dict:
    res = hybrid_retriever.delete_document(filename, tenant_id=tenant_id)
    if not res.get("success"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=res.get("message", f"No se pudo eliminar el documento '{filename}'.")
        )
    return res


@app.post("/api/documents/delete", tags=["Knowledge Base"])
def delete_document_json(
    req: DeleteDocumentRequest,
    current_user: TokenPayload = Depends(require_role(Role.CONFIDENTIAL))
):
    """Elimina un documento concreto ('filename') o toda la base ('all': true, solo admin)."""
    if req.all:
        if current_user.role != Role.ADMIN.value:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Vaciar la base de conocimiento requiere rol 'admin'."
            )
        from app.services.knowledge_base import reset_all
        # Solo vacía la organización del usuario: un admin no puede borrar datos de otra.
        if not reset_all(tenant_id=current_user.tenant_id):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error al restablecer la base de conocimiento."
            )
        return {
            "status": "success",
            "message": "Todos los documentos y el Grafo de Conocimiento han sido eliminados con éxito."
        }

    if not req.filename or not req.filename.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Debe proporcionar el nombre del archivo a eliminar en 'filename' o 'all: true'."
        )
    return _delete_single(req.filename.strip(), current_user.tenant_id)


@app.delete("/api/documents/{filename:path}", tags=["Knowledge Base"])
def delete_single_document(
    filename: str,
    current_user: TokenPayload = Depends(require_role(Role.CONFIDENTIAL))
):
    return _delete_single(filename, current_user.tenant_id)


# =========================================================================
# Asistente, métricas, grafo 3D y voz
# =========================================================================
@app.get("/api/suggested-questions", tags=["Assistant"])
async def get_suggested_questions(current_user: TokenPayload = Depends(get_current_user)):
    from app.services.suggestion_service import get_adaptive_suggestions
    return await get_adaptive_suggestions(tenant_id=current_user.tenant_id)


@app.get("/api/metrics", tags=["Monitoring"])
async def get_metrics(current_user: TokenPayload = Depends(get_current_user)):
    from app.services.metrics_service import collect_all_metrics
    return await collect_all_metrics(tenant_id=current_user.tenant_id)


@app.get("/api/graph-data", tags=["Graph Visualization"])
async def get_graph_data(
    max_nodes: int = Query(500, ge=1, le=2000),
    current_user: TokenPayload = Depends(get_current_user)
):
    from app.services.graph_viz_service import get_graph_data_for_viz
    return await get_graph_data_for_viz(max_nodes=max_nodes, tenant_id=current_user.tenant_id)


@app.post("/api/transcribe", tags=["Voice"])
async def transcribe_audio(
    file: UploadFile = File(...),
    current_user: TokenPayload = Depends(get_current_user)
):
    """Transcribe audio (webm, wav, mp3, m4a, ogg) con Whisper local."""
    from app.services.whisper_service import transcribe_audio as do_transcribe

    ext = _file_extension(file.filename or "audio.webm") or "webm"
    if ext not in ALLOWED_AUDIO_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Formato de audio no soportado: .{ext}.")

    audio_bytes = await _read_upload_limited(file, MAX_AUDIO_SIZE_BYTES)
    result = await do_transcribe(audio_bytes=audio_bytes, audio_format=ext)

    if result.get("error"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Error en la transcripción: {result['error']}"
        )
    return result
