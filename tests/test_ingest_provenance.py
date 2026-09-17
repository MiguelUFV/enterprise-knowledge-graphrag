"""
La procedencia se pierde con facilidad: si los fragmentos llegan a ChromaDB sin
'filename', las respuestas citan un documento genérico y el usuario no puede
verificar nada. Estos tests fijan ese contrato.
"""
import asyncio

import pytest

from app.services import knowledge_base as kb


@pytest.fixture
def payload_capturado(monkeypatch):
    capturado = {}

    monkeypatch.setattr(kb.hybrid_retriever, "add_document_chunks_batch",
                        lambda chunks, tenant_id="default": capturado.update(chunks=chunks, tenant_id=tenant_id))
    monkeypatch.setattr(kb.graph_manager, "get_entity_names", lambda limit=10000, tenant_id="default": [])
    monkeypatch.setattr(kb.graph_manager, "upsert_entities_batch",
                        lambda ents, fn, tenant_id="default": capturado.update(grafo_tenant=tenant_id) or len(ents))

    async def _extraccion_simulada(*a, **kw):
        return '{"entities": [{"entity_name": "Acme Industrial", "entity_type": "Organization"}]}'
    monkeypatch.setattr(kb, "get_llm_response", _extraccion_simulada)

    return capturado


TEXTO = """# Memoria Anual

## Identificación
Acme Industrial S.A. es un fabricante fundado en 1998 con sede en Valladolid
y una plantilla de catorce personas repartidas en tres centros de trabajo.

## Contratos
El contrato CNT-2025-018 con Delta Logistics cubre el suministro diario a
veintidós clientes por 4.300 euros mensuales y vence en diciembre de 2027.
"""


def test_los_fragmentos_indexados_conservan_el_nombre_del_documento(payload_capturado):
    asyncio.run(kb.process_file_content("memoria_anual_2025.md", TEXTO))

    chunks = payload_capturado["chunks"]
    assert chunks, "el documento debería producir al menos un fragmento"
    for c in chunks:
        assert c["metadata"]["filename"] == "memoria_anual_2025.md"


def test_los_fragmentos_conservan_seccion_e_indice(payload_capturado):
    """header_path alimenta la cita '<archivo>#<sección>' de las respuestas."""
    asyncio.run(kb.process_file_content("memoria_anual_2025.md", TEXTO))

    chunks = payload_capturado["chunks"]
    assert all("chunk_index" in c["metadata"] for c in chunks)
    assert any(c["metadata"].get("header_path") for c in chunks)


def test_la_ingesta_se_dirige_a_la_organizacion_indicada(payload_capturado):
    """Un documento subido por 'acme' no puede acabar en el corpus ni en el grafo de otra."""
    asyncio.run(kb.process_file_content("memoria_anual_2025.md", TEXTO, tenant_id="acme"))

    assert payload_capturado["tenant_id"] == "acme"
    assert payload_capturado["grafo_tenant"] == "acme"
