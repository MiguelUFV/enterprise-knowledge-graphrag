"""
Coherencia entre lo que la respuesta dice y lo que el sistema declara sobre ella.

Una respuesta que reconoce no tener el dato no puede reportarse con evidencia
suficiente: la interfaz mostraría "evidencia suficiente" junto a un "no consta",
y esa contradicción destruye la credibilidad de toda la señal de confianza.
"""
import pytest

from app.core.evidence import LEVEL_D_NO_EVIDENCE, is_abstention


@pytest.mark.parametrize("texto", [
    "No consta en la documentación información sobre la penalización del contrato.",
    "No se dispone de datos sobre ese proveedor.",
    "No existe registro de esa incidencia en el corpus.",
    "El corpus no contiene información sobre lo solicitado.",
    "No se encuentra ninguna referencia a ese proyecto.",
    "No hay información en los documentos indexados.",
])
def test_reconoce_una_abstencion(texto):
    assert is_abstention(texto)


@pytest.mark.parametrize("texto", [
    "La penalización del contrato CNT-2025-018 es del 8% por retraso.",
    "Rosa Ibáñez dirige la empresa desde 2012.",
    "El obrador produce 1.900 piezas diarias.",
])
def test_no_confunde_una_respuesta_real_con_una_abstencion(texto):
    assert not is_abstention(texto)


def test_sin_texto_no_hay_abstencion():
    assert not is_abstention("")
    assert not is_abstention(None)


def test_la_ruta_rapida_no_declara_respaldada_una_abstencion():
    """Regresión: FAST_PATH reportaba 0,9 de confianza sobre un 'no consta'."""
    from app.agent.graph_agent import evaluate_and_ground
    import asyncio

    estado = {
        "question": "¿Qué penalización tiene el contrato de MegaRetail?",
        "response": "No consta en la documentación información sobre esa penalización.",
        "is_fast_path_verified": True,
        "hybrid_context": {"vector_passages": [
            {"text": "fragmento", "metadata": {"filename": "doc.md"}}
        ]},
        "uncertainty_level": "LEVEL_A_SUFFICIENT",
        "iteration": 0,
    }

    res = asyncio.run(evaluate_and_ground(estado))

    assert res["confidence_score"] == 0.0, "una abstención no puede tener confianza alta"
    assert res["uncertainty_level"] == LEVEL_D_NO_EVIDENCE
    assert res["is_faithful"] is True, "abstenerse es honesto, no una alucinación"


@pytest.mark.parametrize("texto", [
    "La penalización no está especificada en la documentación, aunque sí constan otros contratos.",
    "El importe no se especifica; la documentación solo menciona la vigencia.",
    "Ese dato no figura en los documentos indexados.",
])
def test_una_respuesta_que_admite_que_falta_el_dato_no_es_nivel_a(texto):
    """
    Una parcial matizada ('no está especificada, pero…') no puede mostrarse como
    evidencia suficiente: la franja diría "suficiente" sobre un dato que falta.
    """
    assert is_abstention(texto)
