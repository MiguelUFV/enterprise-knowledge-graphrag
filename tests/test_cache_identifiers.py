"""
La caché semántica no puede responder a una pregunta con la respuesta de otra.

Regresión medida con eval/evaluar.py: de 8 preguntas sin respuesta en el corpus, el
sistema se inventaba 2. Las dos venían de la caché, en 0,3 s, etiquetadas como
"evidencia suficiente":

    "¿presupuesto del proyecto NEXUS-RESEARCH-009?"  ->  la respuesta de -001
    "¿cómo se resolvió el incidente INC-2024-015?"   ->  la de INC-2025-047

Dos preguntas que solo se diferencian en un código son casi el mismo vector, así que
superaban el umbral de 0,92. La caché saltaba por encima de la recuperación, del
nivel de evidencia y del evaluador: justamente la invención que el sistema promete
no cometer.
"""
import pytest
from dotenv import load_dotenv

load_dotenv()

from app.db.cache_manager import _identificadores, cache_manager

TENANT = "zztest_cache_ids"


@pytest.fixture(autouse=True)
def cache_limpia():
    cache_manager.invalidate_all(tenant_id=TENANT)
    yield
    cache_manager.invalidate_all(tenant_id=TENANT)


def _guardar(pregunta, respuesta):
    cache_manager.set_cached_answer(
        pregunta, respuesta, ["doc.md"], 0.9, "LEVEL_A_SUFFICIENT", "HYBRID_PATH", tenant_id=TENANT
    )


# --- El criterio ------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("presupuesto del proyecto NEXUS-RESEARCH-001", "presupuesto del proyecto NEXUS-RESEARCH-009"),
    ("como se resolvio el incidente INC-2026-001", "como se resolvio el incidente INC-2024-015"),
    ("penalizacion del contrato CNT-2021-BCP-001", "penalizacion del contrato CNT-2019-ABC-777"),
    ("contrato con SLA de 99.9% de uptime", "contrato con SLA de 99.99% de uptime"),
    ("resultados del ejercicio 2025", "resultados del ejercicio 2026"),
])
def test_un_codigo_distinto_hace_preguntas_distintas(a, b):
    assert _identificadores(a) != _identificadores(b)


@pytest.mark.parametrize("a,b", [
    ("¿Quien es la CISO?", "¿Quien ocupa el puesto de CISO?"),
    ("¿Cual es la politica de cancelacion?", "Explica la politica de cancelacion"),
])
def test_una_reformulacion_sin_cifras_sigue_siendo_la_misma_pregunta(a, b):
    assert _identificadores(a) == _identificadores(b)


def test_los_identificadores_no_distinguen_mayusculas_ni_puntuacion_final():
    assert _identificadores("contrato CNT-2021-BCP-001.") == _identificadores("contrato cnt-2021-bcp-001")


# --- El comportamiento ------------------------------------------------------

def test_la_cache_no_responde_sobre_un_codigo_por_otro():
    _guardar(
        "¿Cual es el presupuesto del proyecto NEXUS-RESEARCH-001?",
        "El presupuesto asignado es de 280.000 EUR.",
    )
    fuga = cache_manager.get_cached_answer(
        "¿Cual es el presupuesto del proyecto NEXUS-RESEARCH-009?", tenant_id=TENANT
    )
    assert fuga is None, "la caché respondió sobre un proyecto inexistente con los datos de otro"


def test_la_cache_sigue_sirviendo_la_misma_pregunta():
    """El arreglo no puede dejar la caché inservible: su valor es absorber repeticiones."""
    pregunta = "¿Cual es el presupuesto del proyecto NEXUS-RESEARCH-001?"
    _guardar(pregunta, "280.000 EUR.")
    acierto = cache_manager.get_cached_answer(pregunta, tenant_id=TENANT)
    assert acierto and "280.000" in acierto["text"]
