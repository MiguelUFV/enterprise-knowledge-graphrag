import pytest

from app.core.query_router import QueryRouter


TENANT = "acme"


@pytest.fixture
def router(monkeypatch):
    r = QueryRouter()
    r.dynamic_entities[TENANT] = ["megaretail iberia", "customer360 agent", "elena rostova"]
    r._synced_tenants.add(TENANT)
    monkeypatch.setattr(r, "lookup_dynamic_entities_in_graph", lambda q, tenant_id=None: [])
    return r


def test_exact_identifier_goes_fast_path(router):
    info = router.classify_pre_retrieval("¿Qué dice el contrato CNT-2023-MRI-008?", tenant_id=TENANT)
    assert info["route"] == "FAST_PATH"


def test_relational_question_goes_full_graph(router):
    info = router.classify_pre_retrieval(
        "¿Cuál es la relación entre MegaRetail Iberia y Customer360 Agent?", tenant_id=TENANT
    )
    assert info["route"] == "FULL_GRAPH_RAG"


def test_global_question_is_flagged(router):
    info = router.classify_pre_retrieval("¿Qué tienen en común los documentos?", tenant_id=TENANT)
    assert info["route"] == "FULL_GRAPH_RAG" and info["is_global_query"] is True


def test_default_route_is_hybrid(router):
    info = router.classify_pre_retrieval("Explícame la estrategia de la empresa", tenant_id=TENANT)
    assert info["route"] == "HYBRID_PATH"


def test_el_catalogo_de_una_organizacion_no_alcanza_a_otra(router):
    """Las entidades de 'acme' no deben reconocerse al consultar como otra organización."""
    info = router.classify_pre_retrieval(
        "¿Cuál es la relación entre MegaRetail Iberia y Customer360 Agent?", tenant_id="otra_empresa"
    )
    assert info["detected_entities"] == []


def test_fast_path_escalates_on_weak_evidence(router):
    ok, _, passages = router.verify_fast_path_sufficiency(
        query="precio",
        bm25_results=[{"doc_id": "a", "text": "t", "bm25_score": 1.0}, {"doc_id": "b", "text": "t", "bm25_score": 0.9}],
        vector_results=[{"doc_id": "c", "text": "t", "similarity": 0.4}],
    )
    assert ok is False and passages == []
