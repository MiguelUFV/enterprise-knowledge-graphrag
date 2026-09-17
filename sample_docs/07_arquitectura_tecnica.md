# ARQUITECTURA TÉCNICA DETALLADA — GRAPHRAG ENTERPRISE ENGINE v2.1
## NexusAI Solutions S.L. — Documentación de Ingeniería
### Estado: PRODUCCIÓN | Actualización: Agosto 2026

---

## 1. VISIÓN GENERAL DEL SISTEMA

El GraphRAG Enterprise Engine de NexusAI es un sistema de recuperación aumentada por generación (RAG) que combina tres fuentes de conocimiento:

1. **Grafo de Conocimiento (Neo4j AuraDB)**: Almacena entidades, relaciones y propiedades estructuradas
2. **Almacén Vectorial (ChromaDB)**: Indexa fragmentos de texto con embeddings semánticos
3. **Caché Semántica (ChromaDB)**: Evita recomputaciones costosas para queries similares

La gran diferencia frente a RAG convencional es la capacidad de razonar sobre **relaciones** entre entidades, no solo sobre similitud semántica de texto.

---

## 2. STACK TECNOLÓGICO COMPLETO

### Capa de Aplicación
- **Framework Web**: FastAPI 0.111 (Python 3.11)
- **Servidor ASGI**: Uvicorn con workers Gunicorn
- **Orquestación de Agente**: LangGraph 0.2 (StateGraph pattern)
- **Framework LLM**: LangChain 0.2 (componentes de chain y retrieval)

### Capa de Modelos de IA
- **LLM Principal**: Claude 3.5 Sonnet (Anthropic) — configurado via API
- **LLM Alternativo**: GPT-4o (OpenAI) — fallback automático si Anthropic no disponible
- **Modelo de Embeddings**: text-embedding-3-large (OpenAI) — 3072 dimensiones
- **Modelo de Extracción NER**: GPT-4o (prompt engineering) + spaCy es_core_news_lg
- **Modelo de Reranking**: Cohere Rerank API (cohere-rerank-english-v3.0)
- **Modelo de Guardrails**: PromptGuard (Meta, 86M parámetros, on-premise)

### Capa de Datos
- **Grafo de Conocimiento**: Neo4j AuraDB Professional (3 instancias: prod, staging, qa)
  - Versión Neo4j: 5.x
  - Protocolo: Bolt (puerto 7687)
  - Driver Python: neo4j-driver 5.x
  - Plugins activos: APOC (Awesome Procedures on Cypher), GDS (Graph Data Science)
- **Almacén Vectorial**: ChromaDB 0.5.x (auto-alojado en GKE)
  - Colecciones: `document_store` (documentos), `semantic_cache` (caché), `entities` (entidades)
  - Métrica de distancia: cosine similarity
  - Índice: HNSW (Hierarchical Navigable Small World)
- **Base de Datos de Sesiones y Caché de Agente**: Redis 7.x (Google Memorystore)

### Capa de Infraestructura
- **Contenedores**: Docker + GKE Autopilot (Kubernetes gestionado)
- **Gestión de Infraestructura**: Terraform 1.8 (módulos para GKE, Redis, SecretManager)
- **CI/CD Pipeline**: GitHub Actions (test + build + push) → ArgoCD (deploy en K8s)
- **Registry de Contenedores**: Google Artifact Registry
- **CDN y WAF**: Cloudflare Enterprise (proxy inverso, DDoS protection, Bot Management)
- **Balanceador de Carga**: GCP Cloud Load Balancing (HTTP/HTTPS)
- **Certificados SSL**: Let's Encrypt + Cloudflare (automatizados)
- **DNS**: Cloudflare DNS

### Capa de Observabilidad
- **Métricas**: Prometheus 2.x + Grafana 10.x
- **Traces Distribuidos**: OpenTelemetry (SDK Python) → Jaeger
- **Logs**: Cloud Logging (GCP) + LogDNA para análisis avanzado
- **Alertas**: PagerDuty (integrado con Prometheus AlertManager)
- **Uptime Monitoring**: UptimeRobot + Grafana SLO Dashboard

---

## 3. ESQUEMA ONTOLÓGICO NEO4J

### Tipos de Nodos (Labels)
```
:Entity          — Entidad genérica (base de la jerarquía)
  :Person        — Personas físicas (empleados, clientes, socios)
  :Organization  — Empresas, instituciones, departamentos
  :Product       — Productos y servicios de NexusAI
  :Technology    — Tecnologías, frameworks, herramientas
  :Contract      — Contratos comerciales
  :Incident      — Incidentes de seguridad o operativos
  :Policy        — Políticas, procedimientos, normativas
  :Role          — Roles organizacionales
  :Location      — Ciudades, países, regiones
  :Document      — Documentos fuente ingestados
  :Certification — Certificaciones y acreditaciones
  :Event         — Eventos, reuniones, conferencias
  :Feature       — Características de productos
```

### Tipos de Relaciones (Relationship Types)
```
WORKS_AT          — Person → Organization (empleado en)
MANAGES           — Person → Person/Team (gestiona a)
REPORTS_TO        — Person → Person (reporta a)
FOUNDED           — Person → Organization (fundó)
OWNS              — Organization → Organization (posee/participa en)
HAS_CONTRACT      — Organization → Contract (tiene contrato)
SIGNED_BY         — Contract → Person (firmado por)
USES_PRODUCT      — Organization → Product (usa el producto)
HAS_PRICE         — Product → Price (tiene precio)
OFFERS            — Organization → Product (ofrece el producto)
INTEGRATES_WITH   — Product → Technology (se integra con)
BUILT_WITH        — Product → Technology (construido con)
HAS_SLA           — Contract → SLA (tiene nivel de servicio)
CAUSED            — Incident → System (causó impacto en)
RESOLVED_BY       — Incident → Person (resuelto por)
CERTIFIED_BY      — Organization → Certification (certificada por)
LOCATED_IN        — Organization/Person → Location (ubicado en)
PARTNER_OF        — Organization → Organization (socio de)
INVESTED_IN       — Organization → Organization (invirtió en)
DECISION_MADE_IN  — Decision → Event (decisión tomada en)
RESPONSIBLE_FOR   — Person → Decision/Task (responsable de)
APPLIES_TO        — Policy → Product/System (aplica a)
PROTECTS          — Certification/Policy → Asset (protege)
```

### Índices Definidos en Neo4j
```cypher
CREATE INDEX entity_name FOR (e:Entity) ON (e.name);
CREATE INDEX entity_type FOR (e:Entity) ON (e.entity_type);
CREATE INDEX entity_type_date FOR (e:Entity) ON (e.entity_type, e.created_at);
CREATE INDEX person_email FOR (p:Person) ON (p.email);
CREATE INDEX org_cif FOR (o:Organization) ON (o.cif);
CREATE INDEX contract_id FOR (c:Contract) ON (c.contract_id);
CREATE FULLTEXT INDEX entity_fulltext FOR (e:Entity) ON EACH [e.name, e.description];
```

---

## 4. FLUJO DE PROCESAMIENTO DE CONSULTAS

### Paso 1: Recepción y Validación
```
POST /ask 
  ↓ Rate Limiter (slowapi: 10 req/min/IP)
  ↓ Caché Semántica (ChromaDB query → si hit (>0.92 cosine) → respuesta inmediata)
  ↓ Guardrails de Entrada (PromptGuard + regex injection check)
```

### Paso 2: Agente LangGraph (StateGraph)
```
Nodo 1: extract_entities
  — LLM analiza la pregunta y extrae entidades clave
  — Ejemplo: "¿Qué SLA tiene BancoCentral Hispano?" → ["BancoCentral Hispano", "SLA"]

Nodo 2: retrieve_hybrid_context
  — ChromaDB: búsqueda vectorial top-5 (cosine similarity)
  — Neo4j: Cypher 2-hop traversal sobre las entidades extraídas
  — Cohere: Reranking del contexto unificado

Nodo 3: generate_response
  — LLM Claude 3.5 Sonnet recibe contexto enriquecido
  — Prompt incluye: contexto vectorial + contexto de grafo + historial de conversación
  — Genera respuesta con citas de fuentes

Nodo 4: validate_and_cache
  — Guardrails de salida: verificar que la respuesta no contiene datos sensibles
  — Guardar en caché semántica con TTL de 24h
  — Registrar métricas en Prometheus
```

### Paso 3: Respuesta al Cliente
```json
{
  "text": "El contrato de BancoCentral Hispano (CNT-2021-BCP-001) tiene un SLA de 99.9% de uptime garantizado...",
  "sources": ["Neo4j:Contract:CNT-2021-BCP-001", "VectorDB:05_contratos_comerciales.md"],
  "confidence": 0.94,
  "origin": "generation"
}
```

---

## 5. PIPELINE DE INGESTA DE DOCUMENTOS

### Flujo Completo de Ingesta
```
Documento (PDF/TXT/MD)
  ↓ Extracción de texto (PyPDF2 / UTF-8 decode)
  ↓ Chunking (RecursiveCharacterTextSplitter: chunk_size=1000, overlap=200)
  ↓ [PARALELO]
      ↓ ChromaDB: Generación de embeddings (text-embedding-3-large) + UPSERT
      ↓ LLM: Extracción de entidades y relaciones (EXTRACTION_PROMPT)
          ↓ Neo4j: MERGE de entidades y relaciones extraídas
  ↓ Caché semántica: Indexar resumen del documento
  ↓ Log de ingesta en Cloud Logging
```

### Capacidades de Ingesta
- Velocidad de procesamiento: ~200 páginas PDF / minuto (en GKE con 4 workers)
- Tamaño máximo de documento soportado: 500 MB
- Paralelismo: Hasta 10 documentos simultáneos
- Idiomas soportados actualmente: Español, Inglés (Q3 2026: +6 idiomas)

---

## 6. MODELO DE SEGURIDAD EN PROFUNDIDAD

### Capa 1: Red (Cloudflare Enterprise)
- WAF con reglas OWASP Top 10
- Bot Management (Machine Learning)
- DDoS Protection Level 4 y 7
- IP Reputation Database

### Capa 2: Aplicación (FastAPI)
- Rate Limiting (slowapi): 10 req/min general, 5 req/min para /ask sin autenticar
- CORS restringido a dominios autorizados
- Headers de seguridad: HSTS, CSP, X-Frame-Options

### Capa 3: Guardrails de LLM (PromptGuard + Reglas)
- Detección de Prompt Injection (PromptGuard 86M parámetros)
- Regex de patrones conocidos de jailbreak (DAN, PAIR, etc.)
- Detección de codificación maliciosa (Base64, ROT13, Hex ofuscado)
- Clasificador de intención maliciosa (umbral: confidence > 0.85 → rechazo)

### Capa 4: Datos (Neo4j + ChromaDB)
- Neo4j: Autenticación por usuario/contraseña + TLS 1.3 obligatorio
- ChromaDB: Acceso solo desde red interna GKE (no expuesto al exterior)
- Redis: Autenticación con token, sin exposición a internet
- Secretos: Google Secret Manager (rotación automática planificada)

### Capa 5: Operaciones (SOC)
- PagerDuty: On-call rotation (Rodrigo Peña + Tomás Herrera + Mateo Benítez)
- Prometheus AlertManager: 47 alertas configuradas
- Análisis de logs en tiempo real: Cloud Logging + alertas por anomalías
- Pentest externo anual: Última realización — febrero 2026 (S2 Grupo)

---

## 7. CONFIGURACIÓN DE MODELOS LLM

### Parámetros de Producción (Claude 3.5 Sonnet)
```python
LLM_CONFIG = {
    "model": "claude-3-5-sonnet-20241022",
    "temperature": 0.1,          # Baja para maximizar determinismo
    "max_tokens": 2048,          # Respuestas largas pero controladas
    "top_p": 0.95,
    "stop_sequences": ["Human:", "User:"],  # Evitar roleplaying
    "system": SYSTEM_PROMPT      # Prompt de sistema específico por cliente
}
```

### Parámetros de Extracción NER (GPT-4o)
```python
EXTRACTION_CONFIG = {
    "model": "gpt-4o-2024-08-06",
    "temperature": 0.0,          # Determinismo máximo para extracción estructurada
    "max_tokens": 2500,          # JSON puede ser extenso
    "response_format": {"type": "json_object"}  # JSON mode de OpenAI
}
```

### Gestión de Costes LLM (Mayo 2026)
| Proveedor | Uso Mensual | Coste Mensual |
|---|---|---|
| OpenAI (GPT-4o + Embeddings) | ~2.1M tokens input / 850K tokens output | 11.800 USD |
| Anthropic (Claude 3.5 Sonnet) | ~3.8M tokens input / 1.2M tokens output | 8.400 USD |
| Cohere (Rerank API) | 450.000 reranking calls | 1.800 USD |
| **Total LLM** | — | **22.000 USD/mes** |

---

## 8. BENCHMARKS Y MÉTRICAS DE PERFORMANCE

### Latencia (Agosto 2026, percentiles sobre producción)
| Endpoint | P50 | P95 | P99 | Objetivo P95 |
|---|---|---|---|---|
| /ask (cache hit) | 32 ms | 48 ms | 71 ms | <50 ms ✅ |
| /ask (generation) | 1.4 s | 2.1 s | 3.8 s | <3 s ✅ |
| /api/graph-data | 180 ms | 420 ms | 1.1 s | <1 s ⚠️ |
| /api/metrics | 45 ms | 90 ms | 200 ms | <200 ms ✅ |

### Calidad RAG (Evaluación con RAGAS Framework — Julio 2026)
| Métrica | Valor | Benchmark industria |
|---|---|---|
| Faithfulness (sin alucinaciones) | 0.94 | 0.85 |
| Answer Relevancy | 0.91 | 0.82 |
| Context Precision | 0.87 | 0.78 |
| Context Recall | 0.89 | 0.75 |
| Cache Hit Rate | 34% | N/A |
