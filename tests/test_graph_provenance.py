"""
Tests de integración del grafo (requieren Neo4j accesible; si no, se omiten).
Cubren la regresión que borraba entidades preexistentes al eliminar un documento.
Todos los nodos usados llevan prefijo ZZTEST_ y se eliminan al terminar.
"""
import re

import pytest
from dotenv import load_dotenv

load_dotenv()

from app.db.graph_manager import graph_manager, UNKNOWN_ORIGIN

PREFIX = "ZZTEST_"
DOC_A, DOC_B = "zztest_doc_a.txt", "zztest_doc_b.txt"


def _run(query, **params):
    with graph_manager.connect().session() as s:
        return s.run(query, **params).data()


@pytest.fixture(autouse=True)
def limpiar_nodos_de_prueba(requiere_neo4j):
    _run(f"MATCH (n:Entity) WHERE n.name STARTS WITH '{PREFIX}' DETACH DELETE n")
    yield
    _run(f"MATCH (n:Entity) WHERE n.name STARTS WITH '{PREFIX}' DETACH DELETE n")


def _props(name):
    rows = _run("MATCH (n:Entity {name:$n}) RETURN properties(n) AS p", n=name)
    return rows[0]["p"] if rows else None


def test_entidad_exclusiva_se_borra_con_su_documento():
    graph_manager.upsert_entities_batch([{"entity_name": PREFIX + "Solo", "entity_type": "Test"}], source_doc=DOC_A)
    assert _props(PREFIX + "Solo")["source_docs"] == [DOC_A]
    graph_manager.delete_document_entities(DOC_A)
    assert _props(PREFIX + "Solo") is None


def test_entidad_compartida_sobrevive_y_conserva_el_otro_documento():
    ent = {"entity_name": PREFIX + "Compartida", "entity_type": "Test"}
    graph_manager.upsert_entities_batch([ent], source_doc=DOC_A)
    graph_manager.upsert_entities_batch([ent], source_doc=DOC_B)
    assert sorted(_props(PREFIX + "Compartida")["source_docs"]) == sorted([DOC_A, DOC_B])

    graph_manager.delete_document_entities(DOC_A)
    p = _props(PREFIX + "Compartida")
    assert p is not None, "una entidad citada por otro documento no debe borrarse"
    assert p["source_docs"] == [DOC_B]
    assert p["source_doc"] == DOC_B


def test_entidad_preexistente_sin_origen_no_se_pierde():
    """Regresión: nodos antiguos sin source_doc se borraban al eliminar un documento nuevo."""
    _run(f"CREATE (:Entity {{tenant_id:'default', name:'{PREFIX}Legacy', type:'Test'}})"
         f"-[:RELATED_TO]->(:Entity {{tenant_id:'default', name:'{PREFIX}LegacyAmigo'}})")
    graph_manager.upsert_entities_batch([{"entity_name": PREFIX + "Legacy", "entity_type": "Test"}], source_doc=DOC_A)
    assert _props(PREFIX + "Legacy")["source_docs"] == [UNKNOWN_ORIGIN, DOC_A]

    graph_manager.delete_document_entities(DOC_A)
    p = _props(PREFIX + "Legacy")
    assert p is not None, "un nodo anterior al documento no debe borrarse con él"
    assert p["source_docs"] == [UNKNOWN_ORIGIN]
    assert _run(f"MATCH (:Entity {{name:'{PREFIX}Legacy'}})-[r]->() RETURN count(r) AS c")[0]["c"] == 1


def test_destino_de_relacion_recibe_procedencia_y_se_limpia():
    graph_manager.upsert_entities_batch([{
        "entity_name": PREFIX + "Origen", "entity_type": "Test",
        "relations": [{"target_name": PREFIX + "Destino", "target_type": "Test", "relation_type": "USES"}]
    }], source_doc=DOC_A)
    assert _props(PREFIX + "Destino")["source_docs"] == [DOC_A]
    graph_manager.delete_document_entities(DOC_A)
    assert _props(PREFIX + "Destino") is None, "el destino exclusivo del documento debe borrarse con él"


def test_relacion_exclusiva_de_un_documento_se_borra_con_el():
    """La relación desaparece, pero las entidades citadas por otro documento se quedan."""
    base = [{"entity_name": PREFIX + "A", "entity_type": "Test"}, {"entity_name": PREFIX + "B", "entity_type": "Test"}]
    graph_manager.upsert_entities_batch(base, source_doc=DOC_B)
    graph_manager.upsert_entities_batch([{
        "entity_name": PREFIX + "A", "entity_type": "Test",
        "relations": [{"target_name": PREFIX + "B", "target_type": "Test", "relation_type": "USES"}]
    }], source_doc=DOC_A)
    assert _run(f"MATCH (:Entity {{name:'{PREFIX}A'}})-[r:USES]->() RETURN count(r) AS c")[0]["c"] == 1

    graph_manager.delete_document_entities(DOC_A)
    assert _props(PREFIX + "A") is not None and _props(PREFIX + "B") is not None
    assert _run(f"MATCH (:Entity {{name:'{PREFIX}A'}})-[r:USES]->() RETURN count(r) AS c")[0]["c"] == 0


def test_relacion_compartida_por_dos_documentos_sobrevive():
    ent = [{"entity_name": PREFIX + "A", "entity_type": "Test",
            "relations": [{"target_name": PREFIX + "B", "target_type": "Test", "relation_type": "USES"}]}]
    graph_manager.upsert_entities_batch(ent, source_doc=DOC_A)
    graph_manager.upsert_entities_batch(ent, source_doc=DOC_B)
    graph_manager.delete_document_entities(DOC_A)
    rels = _run(f"MATCH (:Entity {{name:'{PREFIX}A'}})-[r:USES]->() RETURN properties(r) AS p")
    assert len(rels) == 1 and rels[0]["p"]["source_docs"] == [DOC_B]


def test_relacion_preexistente_sin_procedencia_no_se_borra():
    _run(f"CREATE (:Entity {{tenant_id:'default', name:'{PREFIX}A'}})"
         f"-[:USES]->(:Entity {{tenant_id:'default', name:'{PREFIX}B'}})")
    graph_manager.upsert_entities_batch([{
        "entity_name": PREFIX + "A", "entity_type": "Test",
        "relations": [{"target_name": PREFIX + "B", "target_type": "Test", "relation_type": "USES"}]
    }], source_doc=DOC_A)
    graph_manager.delete_document_entities(DOC_A)
    assert _run(f"MATCH (:Entity {{name:'{PREFIX}A'}})-[r:USES]->() RETURN count(r) AS c")[0]["c"] == 1


def test_propiedades_del_llm_no_sobrescriben_campos_internos():
    graph_manager.upsert_entities_batch([{
        "entity_name": PREFIX + "Props", "entity_type": "Test",
        "properties": {"name": "SECUESTRADO", "source_docs": ["falso.txt"], "detalle": "ok",
                       "anidado": {"a": 1}, "lista": ["x", "y"]}
    }], source_doc=DOC_A)
    p = _props(PREFIX + "Props")
    assert p["name"] == PREFIX + "Props"
    assert p["source_docs"] == [DOC_A]
    assert p["detalle"] == "ok"
    assert p["lista"] == ["x", "y"]
    assert isinstance(p["anidado"], str)


def test_tipo_de_relacion_se_sanea_contra_inyeccion_cypher():
    graph_manager.upsert_entity_and_relations({
        "entity_name": PREFIX + "Iny", "entity_type": "Test",
        "relations": [{"target_name": PREFIX + "InyDestino", "relation_type": "x]->() DETACH DELETE n //"}]
    }, source_doc=DOC_A)
    tipos = [r["t"] for r in _run(f"MATCH (:Entity {{name:'{PREFIX}Iny'}})-[r]->() RETURN type(r) AS t")]
    assert len(tipos) == 1
    assert re.fullmatch(r"[A-Z0-9_]+", tipos[0]), f"el tipo de relación no se saneó: {tipos[0]!r}"
    assert _props(PREFIX + "InyDestino") is not None
