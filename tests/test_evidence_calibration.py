"""
Calibración del clasificador epistémico.

Los valores de estos tests son medidas reales del cross-encoder sobre documentos en
español: lo documentado puntúa 1e-4 … 7.7e-1 y lo ajeno 0 … 7.9e-3. Si alguien sube los
umbrales pensando en la escala inglesa (0.79–0.99), el sistema vuelve a abstenerse ante
preguntas que sí tienen respuesta, y estos tests lo impiden.
"""
import pytest

from app.core.evidence import (
    LEVEL_A_SUFFICIENT,
    LEVEL_B_PARTIAL,
    LEVEL_C_RELATED_INSUFFICIENT,
    LEVEL_D_NO_EVIDENCE,
    classify_epistemic_uncertainty,
)


def pasajes(rerank, bm25=0.8, vector=0.35):
    return [{
        "text": "fragmento",
        "reranker_score": rerank,
        "rerank_rationale": "Local ONNX",
        "bm25_score": bm25,
        "similarity": vector,
    }]


def nivel(rerank, **kw):
    return classify_epistemic_uncertainty(pasajes(rerank, **kw), [], [])["uncertainty_level"]


# Medidas reales: consultas cuya respuesta SÍ está en el documento
@pytest.mark.parametrize("rerank,consulta", [
    (0.7721, "Que penalizacion tiene el contrato principal"),
    (0.6040, "Que planes de inversion hay previstos"),
    (0.0916, "Que certificaciones de calidad tiene"),
    (0.0218, "Cuantos empleados tiene la empresa"),
    (0.0082, "Donde esta la planta principal"),
    (0.0012, "Quien dirige la empresa"),
    (0.0007, "Cuando se fundo la empresa"),
])
def test_una_pregunta_documentada_no_se_responde_con_abstencion(rerank, consulta):
    assert nivel(rerank) != LEVEL_D_NO_EVIDENCE, f"abstuvo ante '{consulta}' ({rerank})"


# Medidas reales: consultas ajenas al corpus
@pytest.mark.parametrize("rerank,consulta", [
    (0.0, "Quien gano la Champions League en 2019"),
    (0.0, "Cual es la capital de Mongolia"),
    (0.0, "Dame la receta del gazpacho"),
    (0.0, "Quien es el director financiero de Telefonica"),
    (0.0001, "Que turbinas fabrica la empresa"),
])
def test_una_pregunta_ajena_al_corpus_se_abstiene(rerank, consulta):
    assert nivel(rerank) == LEVEL_D_NO_EVIDENCE, f"respondió a '{consulta}' ({rerank})"


def test_bm25_y_vector_altos_no_rescatan_una_consulta_ajena():
    """El solapamiento de esos dos canales es total: no pueden decidir por sí solos."""
    assert nivel(0.0, bm25=4.0, vector=0.52) == LEVEL_D_NO_EVIDENCE


@pytest.mark.parametrize("rerank", [0.0007, 0.0012, 0.0082, 0.0218, 0.049])
def test_la_zona_de_solapamiento_se_marca_como_evidencia_insuficiente(rerank):
    """Ni silencio ni afirmación: se responde avisando de que la evidencia es débil."""
    assert nivel(rerank) == LEVEL_C_RELATED_INSUFFICIENT


def test_por_debajo_del_suelo_de_discriminacion_se_abstiene():
    """
    A 1e-4 puntúan igual una consulta documentada y una ajena: el modelo ya no distingue.
    Se acepta el falso negativo antes que responder sin poder distinguir señal de ruido.
    """
    assert nivel(0.0001) == LEVEL_D_NO_EVIDENCE


@pytest.mark.parametrize("rerank,esperado", [
    (0.90, LEVEL_A_SUFFICIENT),
    (0.50, LEVEL_A_SUFFICIENT),
    (0.20, LEVEL_B_PARTIAL),
])
def test_por_encima_de_la_zona_gris_se_responde_con_normalidad(rerank, esperado):
    assert nivel(rerank) == esperado


def test_un_camino_de_grafo_evita_la_abstencion():
    """Si el grafo conecta la pregunta, hay evidencia aunque el cross-encoder no la vea."""
    res = classify_epistemic_uncertainty(
        pasajes(0.0), [], [{"path_str": "(Acme) -[:MANAGED_BY]-> (Rosa Ibáñez)"}]
    )
    assert res["uncertainty_level"] != LEVEL_D_NO_EVIDENCE


def test_sin_pasajes_ni_grafo_no_hay_nada_que_responder():
    res = classify_epistemic_uncertainty([], [], [])
    assert res["uncertainty_level"] == LEVEL_D_NO_EVIDENCE


def test_el_reranking_omitido_no_cuenta_como_relevancia():
    """En modo bypass, reranker_score contiene el RRF, que no es comparable."""
    bypass = [{"text": "f", "reranker_score": 0.9, "rerank_rationale": "Bypass por identificador",
               "bm25_score": 0.5, "similarity": 0.2}]
    assert classify_epistemic_uncertainty(bypass, [], [])["uncertainty_level"] == LEVEL_D_NO_EVIDENCE
