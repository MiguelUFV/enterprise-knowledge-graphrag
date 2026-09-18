"""
Aislamiento entre organizaciones.

Es la propiedad de seguridad central del sistema multi-inquilino: los documentos, el
grafo y la caché de una empresa no pueden alcanzarse desde otra, ni siquiera con rol
admin. Los tests de Neo4j se omiten si la base no está accesible.
"""
import pytest
from dotenv import load_dotenv

load_dotenv()

from app.core.auth import DEFAULT_TENANT_ID, TokenPayload, create_access_token, normalize_tenant_id, verify_jwt_token
from app.db.graph_manager import graph_manager

PREFIX = "ZZTENANT_"
TENANT_A, TENANT_B = "zztest_empresa_a", "zztest_empresa_b"


def _run(query, **params):
    with graph_manager.connect().session() as s:
        return s.run(query, **params).data()


# --- Identidad --------------------------------------------------------------

def test_el_tenant_viaja_firmado_en_el_token():
    token = create_access_token(user_id="ana", role="admin", tenant_id="acme")
    assert verify_jwt_token(token).tenant_id == "acme"


def test_un_token_antiguo_sin_tenant_cae_en_la_organizacion_por_defecto():
    """Los tokens emitidos antes del particionado no deben heredar acceso a nada nuevo."""
    assert TokenPayload(user_id="ana").tenant_id == DEFAULT_TENANT_ID


@pytest.mark.parametrize("entrada", [
    None, "", "   ",
    "acme corp",            # espacios
    "../otro",              # recorrido de rutas
    "a" * 64,               # demasiado largo
    "tenant'; MATCH (n) DETACH DELETE n //",   # inyección
    "{'$ne': null}",        # operador de consulta
])
def test_un_tenant_id_malformado_nunca_se_propaga(entrada):
    assert normalize_tenant_id(entrada) == DEFAULT_TENANT_ID


@pytest.mark.parametrize("entrada", ["acme", "empresa_2", "grupo-norte", "a.b.c", "X9"])
def test_se_aceptan_identificadores_razonables(entrada):
    assert normalize_tenant_id(entrada) == entrada


# --- Grafo ------------------------------------------------------------------

@pytest.fixture
def dos_empresas(requiere_neo4j):
    """Ambas tienen una entidad con el MISMO nombre: el caso que más fácil se rompe."""
    comun = {"entity_name": PREFIX + "Cliente Principal", "entity_type": "Organization"}
    graph_manager.upsert_entities_batch([comun], source_doc="a.txt", tenant_id=TENANT_A)
    graph_manager.upsert_entities_batch(
        [{**comun, "relations": [{"target_name": PREFIX + "Solo De B", "target_type": "Product",
                                  "relation_type": "USES"}]}],
        source_doc="b.txt", tenant_id=TENANT_B,
    )
    yield
    _run(f"MATCH (n:Entity) WHERE n.name STARTS WITH '{PREFIX}' DETACH DELETE n")


def test_el_mismo_nombre_en_dos_empresas_son_nodos_distintos(dos_empresas):
    filas = _run(
        "MATCH (n:Entity {name:$n}) RETURN n.tenant_id AS t ORDER BY t",
        n=PREFIX + "Cliente Principal",
    )
    assert [f["t"] for f in filas] == sorted([TENANT_A, TENANT_B]), "MERGE fusionó dos organizaciones"


def test_el_subgrafo_de_una_empresa_no_incluye_nodos_de_otra(dos_empresas):
    sub = graph_manager.extract_subgraph([PREFIX + "Cliente Principal"], tenant_id=TENANT_A)
    nombres = {n["properties"].get("name") for n in sub["nodes"]}
    assert PREFIX + "Solo De B" not in nombres, "fuga de entidades entre organizaciones"


def test_las_estadisticas_cuentan_solo_la_organizacion_propia(dos_empresas):
    a = graph_manager.get_graph_stats(tenant_id=TENANT_A)["nodes"]
    b = graph_manager.get_graph_stats(tenant_id=TENANT_B)["nodes"]
    # A tiene 1 entidad; B tiene 2 (la común más el destino de su relación)
    assert a == 1 and b == 2


def test_los_nombres_para_canonicalizar_no_cruzan_organizaciones(dos_empresas):
    nombres_a = graph_manager.get_entity_names(tenant_id=TENANT_A)
    assert PREFIX + "Solo De B" not in nombres_a


def test_borrar_un_documento_de_una_empresa_no_toca_a_la_otra(dos_empresas):
    graph_manager.delete_document_entities("b.txt", tenant_id=TENANT_B)

    quedan_b = _run("MATCH (n:Entity {tenant_id:$t}) WHERE n.name STARTS WITH $p RETURN count(n) AS c",
                    t=TENANT_B, p=PREFIX)[0]["c"]
    quedan_a = _run("MATCH (n:Entity {tenant_id:$t}) WHERE n.name STARTS WITH $p RETURN count(n) AS c",
                    t=TENANT_A, p=PREFIX)[0]["c"]
    assert quedan_b == 0
    assert quedan_a == 1, "borrar en una organización no puede afectar a la otra"


def test_vaciar_una_organizacion_deja_intacta_la_otra(dos_empresas):
    _run("MATCH (n:Entity {tenant_id:$t}) DETACH DELETE n", t=TENANT_A)
    assert graph_manager.get_graph_stats(tenant_id=TENANT_B)["nodes"] == 2


# --- Documentos, índice léxico y caché --------------------------------------

@pytest.fixture
def dos_corpus():
    """Mismo nombre de archivo en ambas empresas, con contenidos que no deben cruzarse."""
    from app.db.hybrid_retriever import hybrid_retriever

    hybrid_retriever.add_document_chunks_batch([{
        "doc_id": "memoria.txt_chunk_0",
        "text": "La tarifa de mantenimiento de Turbinas Aurora asciende a 12.400 euros anuales.",
        "metadata": {"filename": "memoria.txt"},
    }], tenant_id=TENANT_A)
    hybrid_retriever.add_document_chunks_batch([{
        "doc_id": "memoria.txt_chunk_0",
        "text": "La tarifa de mantenimiento de Hornos Boreal asciende a 98.700 euros anuales.",
        "metadata": {"filename": "memoria.txt"},
    }], tenant_id=TENANT_B)
    yield hybrid_retriever
    for t in (TENANT_A, TENANT_B):
        hybrid_retriever.reset_collection(tenant_id=t)


def test_un_archivo_con_el_mismo_nombre_no_pisa_al_de_la_otra_empresa(dos_corpus):
    a = dos_corpus.list_indexed_documents(TENANT_A)
    b = dos_corpus.list_indexed_documents(TENANT_B)
    assert [d["filename"] for d in a] == ["memoria.txt"]
    assert [d["filename"] for d in b] == ["memoria.txt"]
    assert a[0]["chunks_count"] == 1 and b[0]["chunks_count"] == 1


def test_la_busqueda_lexica_no_devuelve_documentos_de_otra_empresa(dos_corpus):
    resultados = dos_corpus.search_bm25("Hornos Boreal", top_k=10, tenant_id=TENANT_A)
    assert all("Boreal" not in r["text"] for r in resultados), "BM25 filtró mal por organización"


def test_la_busqueda_vectorial_no_devuelve_documentos_de_otra_empresa(dos_corpus):
    resultados = dos_corpus.search_vector_store("tarifa de mantenimiento", top_k=10, tenant_id=TENANT_A)
    assert resultados, "la organización A sí tiene un documento que responde"
    assert all("Boreal" not in r["text"] for r in resultados)


def test_borrar_el_documento_de_una_empresa_conserva_el_homonimo_de_la_otra(dos_corpus):
    dos_corpus.delete_document("memoria.txt", tenant_id=TENANT_A)
    assert dos_corpus.list_indexed_documents(TENANT_A) == []
    assert len(dos_corpus.list_indexed_documents(TENANT_B)) == 1


def test_la_cache_no_sirve_a_una_empresa_la_respuesta_de_otra():
    """Dos empresas hacen la misma pregunta: cada una recibe su propia respuesta."""
    from app.db.cache_manager import cache_manager

    pregunta = "¿Cuál es la tarifa anual de mantenimiento?"
    try:
        cache_manager.set_cached_answer(
            pregunta, "12.400 euros", ["memoria.txt"], 0.9, "LEVEL_A_SUFFICIENT", "FAST_PATH",
            tenant_id=TENANT_A,
        )
        assert cache_manager.get_cached_answer(pregunta, tenant_id=TENANT_B) is None, \
            "fuga de caché: B recibió la respuesta construida con los documentos de A"

        propia = cache_manager.get_cached_answer(pregunta, tenant_id=TENANT_A)
        assert propia and propia["text"] == "12.400 euros"
    finally:
        cache_manager.invalidate_all(tenant_id=TENANT_A)
        cache_manager.invalidate_all(tenant_id=TENANT_B)
