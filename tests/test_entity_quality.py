import pytest

from app.core.entity_quality import (
    build_canonicalizer,
    canonical_entity_type,
    filter_entities,
    is_valid_entity_name,
    normalize_entity_name,
)

# Casos tomados de la basura real que el LLM metió en el grafo
BASURA = [
    "300%", "ROI", "KPI", "1.250 €", "47", "Employee", "Direct Reports",
    "Gerente de las instalaciones", "Employee and customer data",
    "government and military sectors", "auditoría externa", "Tier 1 systems",
    "el cliente", "los datos", "seguridad", "N/A", "etc",
]

VALIDAS = [
    "Elena Rostova", "OmniCorp Global", "Proyecto Nexus", "Zephyr Vault",
    "CNT-2099-ZPH-777", "INC-2026-001", "ISO-27001", "Protocolo Q-Core",
    "NexusAI Solutions S.L.", "Planta de Berlín", "Dr. Aris Thorne",
    "Política de Seguridad Corporativa 2026", "GDPR", "Gene Sequencer V9",
]


@pytest.mark.parametrize("nombre", BASURA)
def test_rechaza_entidades_basura(nombre):
    ok, motivo = is_valid_entity_name(normalize_entity_name(nombre))
    assert not ok, f"debería rechazarse: {nombre!r}"
    assert motivo


@pytest.mark.parametrize("nombre", VALIDAS)
def test_acepta_entidades_reales(nombre):
    ok, motivo = is_valid_entity_name(normalize_entity_name(nombre))
    assert ok, f"debería aceptarse: {nombre!r} ({motivo})"


@pytest.mark.parametrize("nombre", [
    "Audit Committee", "Audit committee", "Human Resources Department",
    "Data Retention Policies", "Tier 1 Vendors", "Capacitación Anual", "Comité de Auditoría",
])
def test_el_juicio_no_depende_de_las_mayusculas_del_llm(nombre):
    """'Audit committee' y 'Audit Committee' deben correr la misma suerte."""
    ok, _ = is_valid_entity_name(normalize_entity_name(nombre))
    assert not ok, f"{nombre!r} es un sintagma genérico, no una entidad"


@pytest.mark.parametrize("nombre", ["Berlin Testing Facility", "Continuous Learning Initiative", "Comité DEI"])
def test_conserva_sintagmas_con_nombre_propio_o_sigla(nombre):
    ok, motivo = is_valid_entity_name(normalize_entity_name(nombre))
    assert ok, f"{nombre!r} sí identifica algo concreto ({motivo})"


def test_normaliza_espacios_comillas_y_puntuacion():
    assert normalize_entity_name('  "Proyecto  Icarus",  ') == "Proyecto Icarus"
    assert normalize_entity_name("(OmniCorp Global)") == "OmniCorp Global"
    assert normalize_entity_name(None) == ""


def test_tipos_se_mapean_al_vocabulario_controlado():
    assert canonical_entity_type("persona") == "Person"
    assert canonical_entity_type("Empresa") == "Organization"
    assert canonical_entity_type("TipoDeEntidad (ej. Product)") == "Product"
    assert canonical_entity_type("") == "Entity"


def test_filtrado_completo_de_un_lote():
    raw = [
        {"entity_name": " Proyecto Nexus ", "entity_type": "proyecto",
         "relations": [{"target_name": "300%", "relation_type": "HAS_ROI"},
                       {"target_name": "OmniCorp Global", "target_type": "empresa", "relation_type": "PART_OF"}]},
        {"entity_name": "ROI", "entity_type": "Metric"},
        {"entity_name": "Proyecto Nexus", "entity_type": "Project"},  # duplicado
        {"entity_name": "Employee", "entity_type": "Role"},
    ]
    limpias, rechazadas = filter_entities(raw)

    assert [e["entity_name"] for e in limpias] == ["Proyecto Nexus"]
    assert limpias[0]["entity_type"] == "Project"
    assert [r["target_name"] for r in limpias[0]["relations"]] == ["OmniCorp Global"]
    assert limpias[0]["relations"][0]["target_type"] == "Organization"
    assert {n for n, _ in rechazadas} == {"300%", "ROI", "Employee"}


def test_descarta_autorelaciones_y_relaciones_duplicadas():
    raw = [{"entity_name": "Zephyr Vault", "entity_type": "Product", "relations": [
        {"target_name": "Zephyr Vault", "relation_type": "USES"},
        {"target_name": "Helvex Logistics", "target_type": "Organization", "relation_type": "USED_BY"},
        {"target_name": "Helvex Logistics", "target_type": "Organization", "relation_type": "USED_BY"},
    ]}]
    limpias, _ = filter_entities(raw)
    # La autorrelación se descarta y las dos copias de la arista quedan en una sola
    # ('USED_BY' se reorienta a 'USES' desde Helvex, ver test_relation_direction.py).
    assert sum(len(e["relations"]) for e in limpias) == 1


def test_descarta_la_misma_arista_declarada_en_ambos_sentidos():
    """El LLM emite 'A SIGNED_WITH B' y 'B SIGNED_WITH A': es una sola arista."""
    raw = [
        {"entity_name": "CNT-2025-018", "entity_type": "Contract",
         "relations": [{"target_name": "Hostelería Duero", "relation_type": "SIGNED_WITH"}]},
        {"entity_name": "Hostelería Duero", "entity_type": "Organization",
         "relations": [{"target_name": "CNT-2025-018", "relation_type": "SIGNED_WITH"}]},
    ]
    limpias, _ = filter_entities(raw)
    aristas = [(e["entity_name"], r["relation_type"], r["target_name"])
               for e in limpias for r in e["relations"]]
    # Con un contrato en un extremo, el tipo se unifica a HAS_CONTRACT
    # (ver test_relation_direction.py) y las dos copias quedan en una.
    assert aristas == [("Hostelería Duero", "HAS_CONTRACT", "CNT-2025-018")]


def test_conserva_dos_relaciones_distintas_entre_las_mismas_entidades():
    raw = [
        {"entity_name": "Acme Global", "entity_type": "Organization", "relations": [
            {"target_name": "Rosa Ibáñez", "relation_type": "MANAGED_BY"},
            {"target_name": "Rosa Ibáñez", "relation_type": "FOUNDED_BY"},
        ]},
    ]
    limpias, _ = filter_entities(raw)
    assert len(limpias[0]["relations"]) == 2


def test_canonicalizacion_unifica_variantes_con_el_grafo():
    canon = build_canonicalizer(["OmniCorp Global", "BioGenetics LLC", "Proyecto Icarus", "Elena Rostova"])
    assert canon("OmniCorp") == "OmniCorp Global"          # variante corta
    assert canon("omnicorp global") == "OmniCorp Global"   # distinta capitalización
    assert canon("BioGenetics") == "BioGenetics LLC"
    assert canon("Elena Rostova") == "Elena Rostova"
    assert canon("Proyecto Nexus") == "Proyecto Nexus"     # proyecto distinto, no se fusiona
    assert canon("Zephyr Vault") == "Zephyr Vault"         # desconocida, se respeta


def test_no_fusiona_cuando_la_palabra_extra_cambia_el_significado():
    canon = build_canonicalizer(["SLA Penalties", "AES-256 Encryption"])
    assert canon("SLA") == "SLA"            # un SLA no es una penalización por SLA
    assert canon("AES-256") == "AES-256"    # el algoritmo no es el cifrado concreto


def test_si_el_grafo_ya_tiene_ambas_variantes_gana_la_completa():
    canon = build_canonicalizer(["OmniCorp", "OmniCorp Global", "Project Nexus", "Project Nexus Specification"])
    assert canon("OmniCorp") == "OmniCorp Global"
    assert canon("OmniCorp Global") == "OmniCorp Global"
    # Dos palabras y el extra no es un sufijo societario: son cosas distintas
    assert canon("Project Nexus") == "Project Nexus"


def test_variante_larga_con_descripcion_vuelve_al_codigo_conocido():
    canon = build_canonicalizer(["ISO/IEC 27001:2022", "Incident INC-099", "Proyecto Icarus"])
    assert canon("ISO/IEC 27001:2022 Information Security Management") == "ISO/IEC 27001:2022"
    assert canon("Incident INC-099 Report") == "Incident INC-099"
    # Sin código no se recorta: podrían ser entidades distintas
    assert canon("Proyecto Icarus Fase 2") == "Proyecto Icarus Fase 2"


def test_canonicalizacion_se_aplica_a_entidades_y_destinos():
    canon = build_canonicalizer(["OmniCorp Global"])
    limpias, _ = filter_entities(
        [{"entity_name": "OmniCorp", "entity_type": "Organization",
          "relations": [{"target_name": "OmniCorp", "relation_type": "PART_OF"}]}],
        canonicalize=canon,
    )
    assert limpias[0]["entity_name"] == "OmniCorp Global"
    assert limpias[0]["relations"] == []  # tras unificar era una autorrelación


def test_nombres_con_siglas_entre_parentesis_se_conservan():
    assert normalize_entity_name("General Data Protection Regulation (GDPR)") == "General Data Protection Regulation (GDPR)"
    assert normalize_entity_name("Sarbanes-Oxley Act (SOX") == "Sarbanes-Oxley Act"
    assert normalize_entity_name("Business Continuity Plan (BCP),") == "Business Continuity Plan (BCP)"


def test_entradas_malformadas_no_rompen_el_filtro():
    limpias, rechazadas = filter_entities([None, "texto", {"entity_name": 42}, {"sin_nombre": 1}])
    assert limpias == []
    assert len(rechazadas) >= 1
