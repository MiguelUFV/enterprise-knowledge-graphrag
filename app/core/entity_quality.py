"""
Control de calidad de las entidades extraídas por el LLM.

El modelo tiende a devolver como "entidades" métricas sueltas ("300%", "ROI"),
roles genéricos ("Employee", "Direct Reports") o sintagmas descriptivos
("government and military sectors"). Esos nodos ensucian el grafo, generan
relaciones falsas y empeoran la recuperación. Aquí se normalizan y se filtran.
"""
import re
import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

MIN_NAME_LEN = 3
MAX_NAME_LEN = 80
MAX_NAME_WORDS = 9  # "Código de Conducta y Ética Empresarial 2025" es una entidad legítima

# Vocabulario controlado de tipos
CANONICAL_TYPES = {
    "person": "Person", "persona": "Person", "people": "Person", "empleado": "Person", "employee": "Person",
    "organization": "Organization", "organisation": "Organization", "organizacion": "Organization",
    "company": "Organization", "empresa": "Organization", "cliente": "Organization", "customer": "Organization",
    "vendor": "Organization", "proveedor": "Organization",
    "product": "Product", "producto": "Product",
    "service": "Service", "servicio": "Service",
    "project": "Project", "proyecto": "Project",
    "technology": "Technology", "tecnologia": "Technology", "system": "Technology", "sistema": "Technology",
    "policy": "Policy", "politica": "Policy", "regulation": "Policy", "normativa": "Policy", "law": "Policy",
    "contract": "Contract", "contrato": "Contract", "agreement": "Contract", "acuerdo": "Contract",
    "incident": "Incident", "incidente": "Incident", "incidencia": "Incident",
    "location": "Location", "lugar": "Location", "facility": "Location", "instalacion": "Location",
    "document": "Document", "documento": "Document", "report": "Document", "informe": "Document",
    "role": "Role", "rol": "Role", "cargo": "Role",
    "event": "Event", "evento": "Event",
    "certification": "Certification", "certificacion": "Certification",
    "standard": "Certification", "norma": "Certification",
    "plan": "Plan", "programa": "Program", "program": "Program",
    "initiative": "Program", "iniciativa": "Program",
    "framework": "Policy", "act": "Policy", "ley": "Policy",
}

# Palabras que por sí solas no identifican a nadie ni a nada
GENERIC_WORDS = {
    # español
    "el", "la", "los", "las", "un", "una", "de", "del", "y", "o", "en", "para", "por", "con", "a",
    "empleado", "empleados", "cliente", "clientes", "usuario", "usuarios", "persona", "personas",
    "dato", "datos", "informe", "informes", "reporte", "reportes", "documento", "documentos",
    "sistema", "sistemas", "servicio", "servicios", "producto", "productos", "proyecto", "proyectos",
    "empresa", "empresas", "equipo", "equipos", "gerente", "director", "jefe", "responsable",
    "auditoria", "auditorias", "externa", "externo", "interna", "interno", "general", "generales",
    "seguridad", "calidad", "gestion", "proceso", "procesos", "area", "areas", "nivel", "niveles",
    "politica", "politicas", "norma", "normas", "objetivo", "objetivos", "riesgo", "riesgos",
    "instalacion", "instalaciones", "sede", "oficina", "departamento", "personal", "plantilla",
    # inglés
    "the", "and", "or", "of", "for", "with", "in", "to", "a", "an",
    "employee", "employees", "customer", "customers", "user", "users", "person", "people",
    "data", "report", "reports", "document", "documents", "system", "systems", "service", "services",
    "product", "products", "project", "projects", "company", "companies", "team", "teams",
    "manager", "director", "head", "staff", "direct", "reports", "tier", "level", "levels",
    "audit", "audits", "external", "internal", "general", "security", "quality", "process", "processes",
    "policy", "policies", "risk", "risks", "objective", "objectives", "sector", "sectors",
    "facility", "facilities", "office", "department", "government", "military", "corporate",
    # Sustantivos organizativos frecuentes: el LLM los capitaliza de forma arbitraria
    # ('Audit committee' vs 'Audit Committee'), así que el juicio no puede depender de eso.
    "committee", "comite", "vendor", "vendors", "proveedores", "departments", "departamentos",
    "policies", "retention", "retencion", "training", "capacitacion", "testing", "pruebas",
    "annual", "anual", "diversity", "diversidad", "equity", "equidad", "inclusion",
    "resources", "recursos", "human", "humanos", "management", "direccion", "board", "consejo",
    "meeting", "reunion", "budget", "presupuesto", "revenue", "ingresos", "compliance", "cumplimiento",
}

# Nombres concretos que nunca deben ser nodos (métricas, siglas de negocio sin identidad)
BLOCKED_NAMES = {
    "roi", "kpi", "kpis", "sla", "slas", "tco", "ebitda", "capex", "opex", "n/a", "na", "tbd",
    "etc", "otros", "others", "varios", "misc", "unknown", "desconocido", "ninguno", "none",
}

_NUMERIC_ONLY = re.compile(r"^[\d\s.,;:%€$£+\-/()]+$")
_HAS_LETTER = re.compile(r"[a-zA-ZáéíóúüñÁÉÍÓÚÜÑ]")
_CODE_TOKEN = re.compile(r"^(?=.*\d)(?=.*[a-zA-Z])[\w.\-/]+$")  # INC-2099-042, ISO-27001, V9
_YEAR_TOKEN = re.compile(r"^(19|20|21)\d{2}$")


def normalize_entity_name(raw: Any) -> str:
    """Limpia el nombre: espacios, comillas y puntuación de borde."""
    if not isinstance(raw, str):
        return ""
    name = re.sub(r"\s+", " ", raw).strip()
    anterior = None
    while name and name != anterior:
        anterior = name
        # Comillas y puntuación de borde (los paréntesis se tratan aparte: "Ley (SOX)" es válido)
        name = re.sub(r"[\s,;:.\-–—]+$", "", name.strip("\"'“”«»").strip()).strip()
        # Envoltorio completo: "(Acme Industrial)" -> "Acme Industrial"
        if name.startswith("(") and name.endswith(")") and name.count("(") == 1:
            name = name[1:-1].strip()
        # Paréntesis sin cerrar del LLM: "Regulation (GDPR" -> "Regulation"
        elif name.count("(") > name.count(")"):
            name = name[:name.rfind("(")].strip()
        elif name.count(")") > name.count("("):
            name = name.replace(")", "").strip()
    return name


def canonical_entity_type(raw: Any) -> str:
    """Mapea el tipo libre del LLM al vocabulario controlado."""
    if not isinstance(raw, str) or not raw.strip():
        return "Entity"
    key = re.sub(r"[^a-z]", "", raw.strip().lower())
    for token, canonical in CANONICAL_TYPES.items():
        if key == re.sub(r"[^a-z]", "", token):
            return canonical
    for token, canonical in CANONICAL_TYPES.items():
        if token in raw.strip().lower():
            return canonical
    return raw.strip()[:40].title() or "Entity"


def is_valid_entity_name(name: str) -> Tuple[bool, str]:
    """
    Decide si un nombre identifica a una entidad real y devuelve el motivo del rechazo.
    """
    if not name or len(name) < MIN_NAME_LEN:
        return False, "demasiado corto"
    if len(name) > MAX_NAME_LEN:
        return False, "demasiado largo (parece una descripción)"
    if not _HAS_LETTER.search(name):
        return False, "sin letras"
    if _NUMERIC_ONLY.match(name):
        return False, "métrica o cifra, no entidad"
    if name.lower() in BLOCKED_NAMES:
        return False, "término de negocio genérico"

    words = name.split()
    if len(words) > MAX_NAME_WORDS:
        return False, "sintagma descriptivo, no entidad"

    # Debe contener al menos un token distintivo: nombre propio, acrónimo o código
    for w in words:
        token = w.strip(".,;:()[]")
        if not token:
            continue
        base = re.sub(r"[^\wáéíóúüñÁÉÍÓÚÜÑ]", "", token).lower()
        base = (base.replace("á", "a").replace("é", "e").replace("í", "i")
                    .replace("ó", "o").replace("ú", "u").replace("ü", "u"))
        if _CODE_TOKEN.match(token) or _YEAR_TOKEN.match(token):
            return True, ""
        if len(token) >= 2 and token.isupper() and base not in BLOCKED_NAMES:
            return True, ""
        if token[:1].isupper() and base not in GENERIC_WORDS and len(base) >= 3:
            return True, ""

    return False, "genérico (sin nombre propio, acrónimo ni código)"


FUZZY_MERGE_THRESHOLD = 92.0

# Sufijos que solo completan el nombre de una organización ya identificada
CORPORATE_SUFFIX_WORDS = {
    "global", "llc", "inc", "corp", "corporation", "ltd", "limited", "gmbh", "sa", "sl", "slu", "sau",
    "s.a", "s.l", "s.a.", "s.l.", "group", "grupo", "holding", "holdings", "iberia", "international",
    "solutions", "labs", "technologies", "systems", "company", "co",
}


def _tiene_codigo(name: str) -> bool:
    """
    El nombre incluye un identificador inequívoco: 'ISO/IEC 27001:2022', 'INC-099', 'V9'.
    Un número suelto ('2029') no cuenta.
    """
    for w in name.split():
        token = w.strip(",;()[]")
        if re.search(r"\d", token) and (re.search(r"[A-Za-z]", token) or re.search(r"[\-/:.]", token)):
            return True
    return False


def _es_variante_corta(corto: str, largo: str) -> bool:
    """'Acme' -> 'Acme Global': misma raíz y el resto es un sufijo societario."""
    palabras_corto, palabras_largo = corto.split(), largo.split()
    if len(palabras_largo) <= len(palabras_corto):
        return False
    if [w.lower() for w in palabras_largo[:len(palabras_corto)]] != [w.lower() for w in palabras_corto]:
        return False
    # Solo se fusiona si lo añadido es un sufijo societario ('Global', 'LLC', 'S.L.').
    # Cualquier otra palabra puede cambiar el significado: 'SLA' no es 'SLA Penalties'.
    extra = [re.sub(r"[^\w.]", "", w).lower() for w in palabras_largo[len(palabras_corto):]]
    return bool(extra) and all(w in CORPORATE_SUFFIX_WORDS for w in extra)


def build_canonicalizer(existing_names: List[str]):
    """
    Devuelve una función que unifica variantes con las entidades ya presentes en el grafo:
    'Acme' -> 'Acme Global', 'Delta Logistics' -> 'Delta Logistics GmbH'.
    Evita que cada documento cree su propio duplicado del mismo actor.
    """
    from rapidfuzz import fuzz

    canon = [n for n in existing_names if isinstance(n, str) and n.strip()]
    por_primer_token: Dict[str, List[str]] = {}
    for n in canon:
        por_primer_token.setdefault(n.split()[0].lower(), []).append(n)

    # Si el grafo ya contiene 'Acme' y 'Acme Global', la forma completa manda:
    # así los duplicados antiguos no se refuerzan con cada documento nuevo.
    preferida: Dict[str, str] = {}
    for corto in canon:
        for largo in por_primer_token.get(corto.split()[0].lower(), []):
            if _es_variante_corta(corto, largo):
                actual = preferida.get(corto.lower())
                if actual is None or len(largo) > len(actual):
                    preferida[corto.lower()] = largo

    por_minusculas = {n.lower(): n for n in canon}

    def canonicalize(name: str) -> str:
        if not name:
            return name
        lower = name.lower()
        if lower in preferida:
            return preferida[lower]
        if lower in por_minusculas:
            return por_minusculas[lower]

        for candidato in por_primer_token.get(lower.split()[0], []):
            # Variante corta de un nombre ya conocido: 'Acme' -> 'Acme Global'
            if _es_variante_corta(name, candidato):
                return preferida.get(candidato.lower(), candidato)
            # Variante larga con descripción pegada: 'ISO/IEC 27001:2022 Information Security' -> 'ISO/IEC 27001:2022'
            if _tiene_codigo(candidato) and lower.startswith(candidato.lower() + " "):
                return candidato

        mejor, mejor_score = None, 0.0
        for candidato in canon:
            score = fuzz.token_sort_ratio(lower, candidato.lower())
            if score > mejor_score:
                mejor, mejor_score = candidato, score
        return mejor if mejor and mejor_score >= FUZZY_MERGE_THRESHOLD else name

    return canonicalize


# --- Orientación canónica de las relaciones ---------------------------------
#
# El LLM elige el sujeto y el objeto de cada relación sin criterio estable: emite
# 'ISO 22000 HAS_CERTIFICATION Empresa' tan a menudo como el sentido correcto. Con un
# grafo así, recorrerlo en una dirección concreta deja de significar nada. Estas dos
# tablas reorientan las aristas de forma determinista, a partir del tipo de las entidades.

# Sinónimos en voz activa: se renombran a su forma pasiva invirtiendo la arista, para que
# el grafo no mezcle 'A MANAGES B' con 'B MANAGED_BY A' como si fueran relaciones distintas.
RELACIONES_INVERSAS = {
    "MANAGES": "MANAGED_BY",
    "LEADS": "LED_BY",
    "DIRECTS": "DIRECTED_BY",
    "AUDITS": "AUDITED_BY",
    "CERTIFIES": "CERTIFIED_BY",
    "FINANCES": "FINANCED_BY",
    "FUNDS": "FINANCED_BY",
    "SUPPLIES": "SUPPLIED_BY",
    "PROVIDES": "PROVIDED_BY",
    "PRODUCES": "PRODUCED_BY",
    "MANUFACTURES": "PRODUCED_BY",
    "OWNS": "OWNED_BY",
    "OPERATES": "OPERATED_BY",
    "EMPLOYS": "EMPLOYED_BY",
    "RESOLVES": "RESOLVED_BY",
    "CONTAINS": "PART_OF",
    "INCLUDES": "PART_OF",
    "USED_BY": "USES",
}

# Tipo de entidad que debe ocupar el DESTINO de cada relación. Solo se invierte cuando hay
# evidencia inequívoca: el origen es del tipo que corresponde al destino y el destino no lo es.
# Si el destino ya encaja, la arista se respeta aunque el origen también encajara.
DESTINO_CANONICO = {
    "HAS_CERTIFICATION": {"Certification"},
    "CERTIFIED_BY": {"Organization", "Person"},
    "AUDITED_BY": {"Organization", "Person"},
    "MANAGED_BY": {"Person"},
    "LED_BY": {"Person"},
    "DIRECTED_BY": {"Person"},
    "FOUNDED_BY": {"Person"},
    "HAS_CEO": {"Person"},
    "HAS_MANAGER": {"Person"},
    "HAS_DIRECTOR": {"Person"},
    "RESOLVED_BY": {"Organization", "Person"},
    "FINANCED_BY": {"Organization"},
    "SUPPLIED_BY": {"Organization"},
    "PROVIDED_BY": {"Organization"},
    "PRODUCED_BY": {"Organization"},
    "OWNED_BY": {"Organization", "Person"},
    "OPERATED_BY": {"Organization", "Person"},
    "EMPLOYED_BY": {"Organization"},
    "WORKS_AT": {"Organization"},
    "WORKS_FOR": {"Organization"},
    "LOCATED_IN": {"Location"},
    "BASED_IN": {"Location"},
    "HAS_CONTRACT": {"Contract"},
    "HAS_INCIDENT": {"Incident"},
    "USES": {"Technology", "Product", "Service"},
}


# Un mismo hecho descrito de dos formas ('CNT-18 SIGNED_WITH Acme' y 'Acme HAS_CONTRACT
# CNT-18') crea dos aristas para una sola realidad. Cuando uno de los extremos delata de
# qué se habla, el tipo se unifica y el deduplicado posterior colapsa el par.
TIPO_SEGUN_EXTREMOS = {
    ("SIGNED_WITH", "Contract"): "HAS_CONTRACT",
    ("PARTY_TO", "Contract"): "HAS_CONTRACT",
    ("AFFECTED_BY", "Incident"): "HAS_INCIDENT",
    ("REPORTED_IN", "Incident"): "HAS_INCIDENT",
}


def orient_relation(
    source: str, source_type: str, target: str, target_type: str, rel_type: str
) -> Tuple[str, str, str, str, str]:
    """
    Devuelve (origen, tipo_origen, destino, tipo_destino, relacion) en orientación canónica.

    'ISO 22000' (Certification) HAS_CERTIFICATION 'Acme' (Organization)
        -> 'Acme' HAS_CERTIFICATION 'ISO 22000'
    """
    rel_type = (rel_type or "RELATED_TO").upper()

    for extremo in (source_type, target_type):
        unificado = TIPO_SEGUN_EXTREMOS.get((rel_type, extremo))
        if unificado:
            rel_type = unificado
            break

    if rel_type in RELACIONES_INVERSAS:
        rel_type = RELACIONES_INVERSAS[rel_type]
        source, target = target, source
        source_type, target_type = target_type, source_type

    esperado = DESTINO_CANONICO.get(rel_type)
    if esperado and target_type not in esperado and source_type in esperado:
        source, target = target, source
        source_type, target_type = target_type, source_type

    return source, source_type, target, target_type, rel_type


def _reorientar(clean: Dict[str, Dict[str, Any]]) -> int:
    """
    Segunda pasada sobre el lote ya filtrado: reorienta cada arista con el tipo real de
    ambos extremos y descarta la misma arista declarada dos veces en sentidos opuestos.
    Devuelve el número de aristas corregidas.
    """
    tipos: Dict[str, str] = {k: e["entity_type"] for k, e in clean.items()}
    aristas: List[Tuple[str, str, str, Dict[str, Any]]] = []
    for key, ent in clean.items():
        for rel in ent["relations"]:
            tipos.setdefault(rel["target_name"].lower(), rel["target_type"])
            aristas.append((ent["entity_name"], rel["target_name"], rel["relation_type"], rel["properties"]))
        ent["relations"] = []

    corregidas = 0
    vistas: set = set()
    for origen, destino, rel_type, props in aristas:
        n_origen, _, n_destino, tipo_destino, n_rel = orient_relation(
            origen, tipos.get(origen.lower(), "Entity"),
            destino, tipos.get(destino.lower(), "Entity"),
            rel_type,
        )
        if n_origen != origen:
            corregidas += 1

        par = (min(n_origen.lower(), n_destino.lower()), max(n_origen.lower(), n_destino.lower()), n_rel)
        if par in vistas:
            continue
        vistas.add(par)

        key = n_origen.lower()
        if key not in clean:
            # El nuevo sujeto solo aparecía como destino: se materializa para no perder la arista
            # (Neo4j lo crearía igualmente con MERGE, pero sin tipo ni procedencia).
            clean[key] = {
                "entity_name": n_origen,
                "entity_type": tipos.get(key, "Entity"),
                "properties": {},
                "relations": [],
            }
        clean[key]["relations"].append({
            "target_name": n_destino,
            "target_type": tipo_destino,
            "relation_type": n_rel,
            "properties": props,
        })

    return corregidas


def filter_entities(raw_entities: List[Any], canonicalize=None) -> Tuple[List[Dict[str, Any]], List[Tuple[str, str]]]:
    """
    Normaliza, valida y deduplica las entidades y sus relaciones, y deja cada arista
    en su orientación canónica.

    :return: (entidades limpias, [(nombre_rechazado, motivo)])
    """
    clean: Dict[str, Dict[str, Any]] = {}
    rejected: List[Tuple[str, str]] = []

    for ent in raw_entities:
        if not isinstance(ent, dict):
            continue
        name = normalize_entity_name(ent.get("entity_name"))
        ok, reason = is_valid_entity_name(name)
        if not ok:
            rejected.append((name or str(ent.get("entity_name"))[:40], reason))
            continue
        if canonicalize:
            name = canonicalize(name)

        key = name.lower()
        if key not in clean:
            clean[key] = {
                "entity_name": name,
                "entity_type": canonical_entity_type(ent.get("entity_type")),
                "properties": ent.get("properties", {}),
                "relations": [],
            }

        seen_rel = {(r["target_name"].lower(), r["relation_type"]) for r in clean[key]["relations"]}
        for rel in ent.get("relations", []) or []:
            if not isinstance(rel, dict):
                continue
            target = normalize_entity_name(rel.get("target_name"))
            t_ok, t_reason = is_valid_entity_name(target)
            if not t_ok:
                rejected.append((target or str(rel.get("target_name"))[:40], f"destino de relación: {t_reason}"))
                continue
            if canonicalize:
                target = canonicalize(target)
            if target.lower() == key:
                continue  # sin auto-relaciones
            rel_type = str(rel.get("relation_type") or "RELATED_TO")[:40]
            if (target.lower(), rel_type) in seen_rel:
                continue
            seen_rel.add((target.lower(), rel_type))
            clean[key]["relations"].append({
                "target_name": target,
                "target_type": canonical_entity_type(rel.get("target_type")),
                "relation_type": rel_type,
                "properties": rel.get("properties", {}),
            })

    corregidas = _reorientar(clean)
    if corregidas:
        logger.info(f"Orientación de relaciones: {corregidas} aristas corregidas de sentido.")

    return list(clean.values()), rejected
