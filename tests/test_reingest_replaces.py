"""
Volver a subir un documento lo sustituye; no lo superpone.

Regresión: la reingesta escribía encima en ChromaDB pero no tocaba el índice léxico.
BM25 ignora los doc_id que ya conoce, así que conservaba el texto de la versión
anterior, y los fragmentos que la nueva versión ya no produce seguían siendo
recuperables. Una respuesta podía citar, con su fuente y su nivel de evidencia, una
frase que ya no estaba en el documento.
"""
import asyncio

import pytest
from dotenv import load_dotenv

load_dotenv()

from app.db.hybrid_retriever import hybrid_retriever
from app.services import knowledge_base

TENANT = "zztest_reingesta"
DOC = "politica_precios.md"

V1 = ("# Tarifas\n\n"
      "La tarifa de mantenimiento asciende a 12400 euros anuales.\n\n"
      "# Anexo derogado\n\n"
      "El recargo por urgencia zzsentinelaviejo es del 40 por ciento.\n")

V2 = ("# Tarifas\n\n"
      "La tarifa de mantenimiento asciende a 31900 euros anuales.\n")


@pytest.fixture
def sin_llm_ni_grafo(monkeypatch):
    """La reingesta se prueba sin salir a la red: no es lo que está bajo prueba."""
    async def _sin_entidades(*a, **k):
        return '{"entities": []}'

    monkeypatch.setattr(knowledge_base, "get_llm_response", _sin_entidades)
    monkeypatch.setattr(knowledge_base.graph_manager, "get_entity_names", lambda *a, **k: [])
    # graph_manager es el mismo objeto en los dos módulos: basta parchear el atributo.
    monkeypatch.setattr(knowledge_base.graph_manager, "delete_document_entities", lambda *a, **k: 0)
    yield
    hybrid_retriever.reset_collection(tenant_id=TENANT)


def _ingesta(contenido):
    return asyncio.run(knowledge_base.process_file_content(DOC, contenido, TENANT))


def _textos_bm25(consulta):
    return " ".join(r["text"] for r in hybrid_retriever.search_bm25(consulta, top_k=20, tenant_id=TENANT))


def test_la_version_nueva_sustituye_a_la_anterior(sin_llm_ni_grafo):
    _ingesta(V1)
    assert "12400" in _textos_bm25("tarifa mantenimiento anuales")

    _ingesta(V2)
    encontrado = _textos_bm25("tarifa mantenimiento anuales")
    assert "31900" in encontrado, "la nueva cifra debe ser recuperable"
    assert "12400" not in encontrado, "el índice léxico sirvió el texto de la versión anterior"


def test_las_secciones_eliminadas_dejan_de_ser_recuperables(sin_llm_ni_grafo):
    _ingesta(V1)
    assert hybrid_retriever.search_bm25("zzsentinelaviejo", top_k=5, tenant_id=TENANT)

    _ingesta(V2)
    assert not hybrid_retriever.search_bm25("zzsentinelaviejo", top_k=5, tenant_id=TENANT), \
        "un fragmento que la nueva versión ya no contiene siguió siendo citable"


def test_no_quedan_fragmentos_huerfanos_de_la_version_vieja(sin_llm_ni_grafo):
    _ingesta(V1)
    _ingesta(V2)

    docs = hybrid_retriever.list_indexed_documents(TENANT)
    assert len(docs) == 1, "la reingesta duplicó la entrada del documento"
    assert docs[0]["chunks_count"] == len(
        knowledge_base.structured_chunker.chunk_document(V2, DOC)
    ), "quedaron fragmentos de la versión anterior"
