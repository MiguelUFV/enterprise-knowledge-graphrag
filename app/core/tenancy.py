"""
Registro de organizaciones (inquilinos).

Cada organización tiene su propio corpus, grafo y caché; aquí solo vive lo que hay que
mostrar de ella. Se lee de 'tenants.json' en la raíz del proyecto:

    {
      "default":  {"display_name": "Mi Empresa"},
      "acme":     {"display_name": "Acme Industrial S.A."}
    }

Si el archivo no existe, el despliegue atiende a una sola organización y su nombre sale
de COMPANY_NAME. Así el caso mono-inquilino no necesita configuración extra.
"""
import os
import json
import logging
from typing import Dict, Any

from app.core.auth import DEFAULT_TENANT_ID, normalize_tenant_id

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TENANTS_FILE = os.getenv("TENANTS_FILE", os.path.join(BASE_DIR, "tenants.json"))

_cache: Dict[str, Dict[str, Any]] = {}
_cache_mtime: float = -1.0


def _load() -> Dict[str, Dict[str, Any]]:
    """Lee tenants.json, releyéndolo solo si cambió en disco."""
    global _cache, _cache_mtime
    try:
        mtime = os.path.getmtime(TENANTS_FILE)
    except OSError:
        return {}

    if mtime == _cache_mtime:
        return _cache

    try:
        with open(TENANTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("el archivo debe contener un objeto {tenant_id: {...}}")
        _cache = {
            normalize_tenant_id(k): v for k, v in data.items() if isinstance(v, dict)
        }
        _cache_mtime = mtime
        logger.info(f"Registro de organizaciones cargado: {len(_cache)} entradas.")
    except Exception as e:
        logger.warning(f"No se pudo leer '{TENANTS_FILE}': {e}. Se usa la configuración por defecto.")
    return _cache


def tenant_display_name(tenant_id: str) -> str:
    """Nombre que la UI muestra para esta organización."""
    entry = _load().get(tenant_id) or {}
    nombre = str(entry.get("display_name") or "").strip()
    if nombre:
        return nombre
    if tenant_id == DEFAULT_TENANT_ID:
        return os.getenv("COMPANY_NAME", "Enterprise Knowledge").strip() or "Enterprise Knowledge"
    return tenant_id

