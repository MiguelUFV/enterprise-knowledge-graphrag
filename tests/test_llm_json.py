import pytest

from app.core.llm_json import parse_llm_json


def test_json_limpio():
    assert parse_llm_json('{"a": 1}') == {"a": 1}


def test_json_envuelto_en_markdown():
    assert parse_llm_json('```json\n{"entities": [{"entity_name": "X"}]}\n```') == {"entities": [{"entity_name": "X"}]}
    assert parse_llm_json('```\n{"a": [1, 2]}\n```') == {"a": [1, 2]}


def test_comas_finales_sobrantes():
    assert parse_llm_json('{"a": [1, 2,], "b": 3,}') == {"a": [1, 2], "b": 3}


def test_respuesta_truncada_por_max_tokens():
    """El juez se queda sin tokens a mitad de una afirmación: se recupera lo completo."""
    truncado = (
        '{"faithfulness_score": 0.9, "relevancy_score": 0.8, "claims": ['
        '{"claim_text": "Elena Rostova es CEO", "is_grounded": true}, '
        '{"claim_text": "El contrato asciende a 432'
    )
    data = parse_llm_json(truncado)
    assert data["faithfulness_score"] == 0.9
    assert len(data["claims"]) == 1
    assert data["claims"][0]["claim_text"] == "Elena Rostova es CEO"


def test_lista_truncada_de_entidades():
    truncado = '{"entities": [{"entity_name": "OmniCorp Global"}, {"entity_name": "Proyecto Ne'
    assert parse_llm_json(truncado)["entities"] == [{"entity_name": "OmniCorp Global"}]


def test_texto_sin_json_lanza_error():
    with pytest.raises(ValueError):
        parse_llm_json("Lo siento, no puedo ayudarte con eso.")
    with pytest.raises(ValueError):
        parse_llm_json("")
