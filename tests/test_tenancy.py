"""Registro de organizaciones: nombre visible y lectura de tenants.json."""
import json

import pytest

from app.core import tenancy


@pytest.fixture
def registro(tmp_path, monkeypatch):
    """Apunta el registro a un archivo temporal y limpia la caché entre tests."""
    def escribir(data):
        ruta = tmp_path / "tenants.json"
        ruta.write_text(json.dumps(data), encoding="utf-8")
        monkeypatch.setattr(tenancy, "TENANTS_FILE", str(ruta))
        monkeypatch.setattr(tenancy, "_cache", {})
        monkeypatch.setattr(tenancy, "_cache_mtime", -1.0)
        return ruta
    monkeypatch.setattr(tenancy, "_cache", {})
    monkeypatch.setattr(tenancy, "_cache_mtime", -1.0)
    return escribir


def test_sin_registro_el_nombre_sale_de_company_name(monkeypatch, tmp_path):
    monkeypatch.setattr(tenancy, "TENANTS_FILE", str(tmp_path / "no_existe.json"))
    monkeypatch.setattr(tenancy, "_cache", {})
    monkeypatch.setattr(tenancy, "_cache_mtime", -1.0)
    monkeypatch.setenv("COMPANY_NAME", "Panadería La Espiga")
    assert tenancy.tenant_display_name("default") == "Panadería La Espiga"


def test_cada_organizacion_muestra_su_propio_nombre(registro):
    registro({"acme": {"display_name": "Acme Industrial S.A."},
              "delta": {"display_name": "Delta Logistics GmbH"}})
    assert tenancy.tenant_display_name("acme") == "Acme Industrial S.A."
    assert tenancy.tenant_display_name("delta") == "Delta Logistics GmbH"


def test_una_organizacion_no_declarada_usa_su_identificador(registro):
    registro({"acme": {"display_name": "Acme Industrial S.A."}})
    assert tenancy.tenant_display_name("empresa_nueva") == "empresa_nueva"


def test_un_registro_corrupto_no_tumba_la_aplicacion(tmp_path, monkeypatch):
    ruta = tmp_path / "tenants.json"
    ruta.write_text("{esto no es json", encoding="utf-8")
    monkeypatch.setattr(tenancy, "TENANTS_FILE", str(ruta))
    monkeypatch.setattr(tenancy, "_cache", {})
    monkeypatch.setattr(tenancy, "_cache_mtime", -1.0)
    monkeypatch.setenv("COMPANY_NAME", "Respaldo")
    assert tenancy.tenant_display_name("default") == "Respaldo"


def test_el_ejemplo_del_repositorio_es_json_valido():
    import os
    ruta = os.path.join(tenancy.BASE_DIR, "tenants.example.json")
    with open(ruta, encoding="utf-8") as f:
        data = json.load(f)
    assert all(isinstance(v, dict) and v.get("display_name") for v in data.values())
