from app.core.bm25 import OkapiBM25, reciprocal_rank_fusion
from app.core.security_audit import security_auditor


def _doc(doc_id, text):
    return {"doc_id": doc_id, "text": text, "metadata": {"filename": f"{doc_id}.md"}}


def test_bm25_ranks_exact_identifier_first():
    engine = OkapiBM25()
    engine.add_documents([
        _doc("a", "Contrato CNT-2023-MRI-008 con MegaRetail por valor anual de 120.000 euros"),
        _doc("b", "Política de seguridad corporativa y control de accesos"),
        _doc("c", "Incidencia de red resuelta por el equipo de operaciones"),
    ])
    results = engine.search("CNT-2023-MRI-008", top_k=3)
    assert results and results[0]["doc_id"] == "a"


def test_bm25_remove_documents():
    engine = OkapiBM25()
    engine.add_documents([_doc("a", "grafo de conocimiento"), _doc("b", "grafo relacional")])
    engine.remove_documents_by_ids(["a"])
    assert [r["doc_id"] for r in engine.search("grafo")] == ["b"]


def test_rrf_rewards_consensus_between_channels():
    vector = [{"doc_id": "x", "text": "x"}, {"doc_id": "y", "text": "y"}]
    bm25 = [{"doc_id": "y", "text": "y", "bm25_score": 3.0}, {"doc_id": "z", "text": "z", "bm25_score": 1.0}]
    fused = reciprocal_rank_fusion(vector, bm25, k=60, top_k=3)
    assert fused[0]["doc_id"] == "y"
    assert set(fused[0]["origin_channels"]) == {"vector", "bm25"}


def test_sanitize_output_redacts_keys_and_cards():
    text = "clave sk-or-v1-" + "a" * 48 + " y tarjeta 4111 1111 1111 1111"
    out = security_auditor.sanitize_output(text)
    assert "sk-or-v1" not in out and "4111" not in out
    assert "[REDACTED_API_KEY]" in out and "[REDACTED_CARD]" in out
