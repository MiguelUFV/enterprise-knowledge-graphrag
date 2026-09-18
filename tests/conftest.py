import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_disponible = None


def neo4j_disponible() -> bool:
    """
    True solo si Neo4j responde a una consulta real.

    Crear el driver no basta: el cliente conecta de forma perezosa, asi que un
    servidor caido solo se detecta al ejecutar algo. Sin esta comprobacion la
    integracion continua fallaba en vez de omitir los tests que necesitan la base.
    El resultado se calcula una sola vez por sesion.
    """
    global _disponible
    if _disponible is None:
        try:
            from app.db.graph_manager import graph_manager
            with graph_manager.connect().session() as s:
                s.run("RETURN 1").single()
            _disponible = True
        except Exception:
            _disponible = False
    return _disponible


@pytest.fixture
def requiere_neo4j():
    """Omite el test si Neo4j no esta accesible, en vez de hacerlo fallar."""
    if not neo4j_disponible():
        pytest.skip("Neo4j no esta accesible")
