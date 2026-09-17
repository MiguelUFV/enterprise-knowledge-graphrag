from typing import List, Literal, Dict, Any, Optional
from pydantic import BaseModel, Field, ConfigDict


class QueryRequest(BaseModel):
    """
    Modelo Pydantic v2 para la petición de consulta al sistema GraphRAG.
    """
    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "question": "¿Cuáles son los procedimientos de seguridad en la red corporativa?"
            }
        }
    )

    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="Pregunta o consulta del usuario.",
        examples=["¿Cuáles son los procedimientos de seguridad en la red corporativa?"]
    )


class RAGResponse(BaseModel):
    """
    Modelo Pydantic v2 para la respuesta estructurada del sistema GraphRAG.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "text": "Los procedimientos de seguridad establecen el uso obligatorio de MFA y cifrado TLS 1.3.",
                "sources": ["doc_sec_001.pdf#page=4", "graph_node:Policy_88"],
                "confidence": 0.94,
                "origin": "generation"
            }
        }
    )

    text: str = Field(
        ...,
        description="Texto resultante de la respuesta generada o recuperada de la caché."
    )
    sources: List[str] = Field(
        default_factory=list,
        description="Lista de fuentes, documentos o nodos del grafo consultados para responder."
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Índice compuesto de confianza normalizado entre 0.0 y 1.0."
    )
    confidence_score: Optional[float] = Field(
        default=None,
        description="Índice compuesto de verificación de evidencia (0.0 a 1.0)."
    )
    uncertainty_level: Optional[str] = Field(
        default="LEVEL_A_SUFFICIENT",
        description="Nivel epistémico de evidencia: LEVEL_A_SUFFICIENT, LEVEL_B_PARTIAL, LEVEL_C_RELATED_INSUFFICIENT, LEVEL_D_NO_EVIDENCE."
    )
    evidence_grounding: Optional[Dict[str, Any]] = Field(
        default_factory=dict,
        description="Trazabilidad detallada afirmación -> evidencia (citas documentales y caminos de grafo)."
    )
    graph_paths: Optional[List[Dict[str, Any]]] = Field(
        default_factory=list,
        description="Caminos estructurados multi-hop recorridos en Neo4j como evidencia."
    )
    route: Optional[str] = Field(
        default="HYBRID_PATH",
        description="Ruta adaptativa de ejecución: FAST_PATH, HYBRID_PATH o FULL_GRAPH_RAG."
    )
    origin: Literal["cache", "generation"] = Field(
        ...,
        description="Origen de la respuesta: 'cache' si provenía de la caché semántica o 'generation' si fue procesada por la canalización RAG."
    )


class EntityRelation(BaseModel):
    """
    Esquema Pydantic v2 para definir una relación entre la entidad principal y otra entidad de destino.
    """
    target_name: str = Field(..., description="Nombre de la entidad de destino.")
    target_type: str = Field(default="Entity", description="Tipo/Etiqueta de la entidad de destino.")
    relation_type: str = Field(default="RELATED_TO", description="Tipo de relación (ej. APPLIES_TO, BELONGS_TO, OWNS).")
    properties: Dict[str, Any] = Field(default_factory=dict, description="Propiedades adicionales de la relación.")


class IngestPayload(BaseModel):
    """
    Esquema Pydantic v2 para el payload de ingesta automatizada mediante Webhook (n8n / Make / CRM).
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "entity_name": "Política de Seguridad 2026",
                "entity_type": "Document",
                "properties": {
                    "author": "Departamento IT",
                    "version": "2.1"
                },
                "relations": [
                    {
                        "target_name": "Red Corporativa",
                        "target_type": "System",
                        "relation_type": "APPLIES_TO",
                        "properties": {"criticality": "high"}
                    }
                ]
            }
        }
    )

    entity_name: str = Field(..., min_length=1, description="Nombre o identificador principal de la entidad.")
    entity_type: str = Field(default="Entity", description="Tipo o categoría de la entidad (ej. Document, Service, User).")
    properties: Dict[str, Any] = Field(default_factory=dict, description="Atributos o propiedades arbitrarias de la entidad.")
    relations: List[EntityRelation] = Field(default_factory=list, description="Lista de relaciones a conectar en el grafo.")
