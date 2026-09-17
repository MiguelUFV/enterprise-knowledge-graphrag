"""
El LLM invierte el sujeto y el objeto de las relaciones sin criterio estable.
Estos casos salen de extracciones reales sobre documentos que no son de la demo.
"""
import pytest

from app.core.entity_quality import filter_entities, orient_relation


def aristas(entidades):
    return {
        (e["entity_name"], r["relation_type"], r["target_name"])
        for e in entidades for r in e["relations"]
    }


@pytest.mark.parametrize("entrada,esperado", [
    # (origen, tipo_origen, destino, tipo_destino, relacion) -> (origen, destino, relacion)
    (("ISO 22000", "Certification", "Acme Industrial", "Organization", "HAS_CERTIFICATION"),
     ("Acme Industrial", "ISO 22000", "HAS_CERTIFICATION")),
    (("Rosa Ibáñez", "Person", "Acme Industrial", "Organization", "MANAGED_BY"),
     ("Acme Industrial", "Rosa Ibáñez", "MANAGED_BY")),
    (("Caja del Norte", "Organization", "Plan 2026", "Project", "FINANCED_BY"),
     ("Plan 2026", "Caja del Norte", "FINANCED_BY")),
    (("Talleres Pisuerga", "Organization", "INC-2025-003", "Incident", "RESOLVED_BY"),
     ("INC-2025-003", "Talleres Pisuerga", "RESOLVED_BY")),
    (("Calle Santiago", "Location", "Acme Industrial", "Organization", "LOCATED_IN"),
     ("Acme Industrial", "Calle Santiago", "LOCATED_IN")),
    (("AENOR", "Organization", "ISO 22000", "Certification", "AUDITED_BY"),
     ("ISO 22000", "AENOR", "AUDITED_BY")),
])
def test_corrige_aristas_invertidas(entrada, esperado):
    o, _, d, _, r = orient_relation(*entrada)
    assert (o, d, r) == esperado


@pytest.mark.parametrize("entrada", [
    ("Acme Industrial", "Organization", "ISO 22000", "Certification", "HAS_CERTIFICATION"),
    ("Acme Industrial", "Organization", "Rosa Ibáñez", "Person", "MANAGED_BY"),
    ("INC-2025-003", "Incident", "Talleres Pisuerga", "Organization", "RESOLVED_BY"),
    ("Acme Industrial", "Organization", "Polígono Sur", "Location", "LOCATED_IN"),
])
def test_respeta_las_aristas_que_ya_estan_bien(entrada):
    o, _, d, _, r = orient_relation(*entrada)
    assert (o, d, r) == (entrada[0], entrada[2], entrada[4])


def test_no_toca_relaciones_fuera_del_vocabulario():
    """Sin regla declarada no hay forma determinista de decidir: se respeta al LLM."""
    o, _, d, _, r = orient_relation("Acme", "Organization", "Delta Logistics", "Organization", "SIGNED_WITH")
    assert (o, d, r) == ("Acme", "Delta Logistics", "SIGNED_WITH")


def test_el_mismo_hecho_descrito_de_dos_formas_queda_en_una_sola_arista():
    """'CNT SIGNED_WITH Acme' y 'Acme HAS_CONTRACT CNT' describen lo mismo."""
    raw = [
        {"entity_name": "CNT-2025-018", "entity_type": "Contract",
         "relations": [{"target_name": "Acme Industrial", "target_type": "Organization",
                        "relation_type": "SIGNED_WITH"}]},
        {"entity_name": "Acme Industrial", "entity_type": "Organization",
         "relations": [{"target_name": "CNT-2025-018", "target_type": "Contract",
                        "relation_type": "HAS_CONTRACT"}]},
    ]
    limpias, _ = filter_entities(raw)
    assert aristas(limpias) == {("Acme Industrial", "HAS_CONTRACT", "CNT-2025-018")}


def test_los_dos_extremos_del_mismo_tipo_se_respetan():
    """Con Organization a ambos lados no hay señal para decidir el sentido: no se toca."""
    o, _, d, _, r = orient_relation("Bureau Veritas", "Organization", "Acme", "Organization", "AUDITED_BY")
    assert (o, d, r) == ("Bureau Veritas", "Acme", "AUDITED_BY")


def test_la_voz_activa_se_unifica_con_la_pasiva():
    """'A MANAGES B' y 'B MANAGED_BY A' son la misma arista: el grafo guarda una sola forma."""
    o, _, d, _, r = orient_relation("Rosa Ibáñez", "Person", "Acme Industrial", "Organization", "MANAGES")
    assert (o, d, r) == ("Acme Industrial", "Rosa Ibáñez", "MANAGED_BY")


def test_usa_el_tipo_real_de_la_entidad_del_lote_no_el_declarado_en_la_relacion():
    """El 'target_type' que escribe el LLM es poco fiable; manda el tipo de la entidad."""
    raw = [
        {"entity_name": "ISO 22000", "entity_type": "Certification",
         "relations": [{"target_name": "Acme Industrial", "target_type": "Entity",
                        "relation_type": "HAS_CERTIFICATION"}]},
        {"entity_name": "Acme Industrial", "entity_type": "Organization"},
    ]
    limpias, _ = filter_entities(raw)
    assert aristas(limpias) == {("Acme Industrial", "HAS_CERTIFICATION", "ISO 22000")}


def test_la_arista_reorientada_no_se_pierde_si_el_nuevo_sujeto_no_estaba_en_el_lote():
    raw = [{"entity_name": "ISO 22000", "entity_type": "Certification",
            "relations": [{"target_name": "Acme Industrial", "target_type": "Organization",
                           "relation_type": "HAS_CERTIFICATION"}]}]
    limpias, _ = filter_entities(raw)
    assert aristas(limpias) == {("Acme Industrial", "HAS_CERTIFICATION", "ISO 22000")}
    assert {e["entity_name"] for e in limpias} == {"ISO 22000", "Acme Industrial"}


def test_las_dos_direcciones_de_la_misma_arista_colapsan_tras_reorientar():
    raw = [
        {"entity_name": "Acme Industrial", "entity_type": "Organization",
         "relations": [{"target_name": "Rosa Ibáñez", "target_type": "Person", "relation_type": "MANAGED_BY"}]},
        {"entity_name": "Rosa Ibáñez", "entity_type": "Person",
         "relations": [{"target_name": "Acme Industrial", "target_type": "Organization", "relation_type": "MANAGES"}]},
    ]
    limpias, _ = filter_entities(raw)
    assert aristas(limpias) == {("Acme Industrial", "MANAGED_BY", "Rosa Ibáñez")}
