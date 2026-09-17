# CATÁLOGO TÉCNICO DE PRODUCTOS Y SERVICIOS — v3.1
## NexusAI Solutions S.L.
### Documento Técnico Interno — Clasificación: CONFIDENCIAL

---

## PRODUCTO 1: Customer360 Agent — Agente Inteligente de Atención al Cliente

### Descripción Ejecutiva
Customer360 es el producto flagship de NexusAI Solutions. Es un sistema de atención al cliente basado en IA conversacional avanzada que combina memoria conversacional de largo plazo, recuperación híbrida RAG y un grafo de conocimiento actualizado en tiempo real para ofrecer respuestas precisas, contextuales y personalizadas.

### Arquitectura Técnica
- **Capa de Orquestación**: LangGraph (StateGraph con nodos de razonamiento, recuperación y síntesis)
- **LLM Base**: Claude 3.5 Sonnet (Anthropic) / GPT-4o (OpenAI) — configurable por cliente
- **Memoria Conversacional**: Redis (TTL 24h) + Neo4j (memoria a largo plazo persistente)
- **Vector Store**: ChromaDB (embeddings con text-embedding-3-large de OpenAI)
- **Grafo de Conocimiento**: Neo4j AuraDB — ontología personalizada por cliente
- **Guardrails**: Capa de validación con PromptGuard (Meta) + reglas personalizadas en Python

### Integraciones Nativas (out-of-the-box)
- CRM: Salesforce Sales Cloud, HubSpot CRM, Microsoft Dynamics 365
- Mensajería: WhatsApp Business API (360dialog), Telegram Bot API, Slack Bolt SDK
- Ticketing: Zendesk Suite, Freshdesk, ServiceNow
- Email: SendGrid, Mailchimp Transactional
- E-commerce: Shopify (via Webhooks), WooCommerce REST API

### Planes y Precios
| Plan | Consultas/mes | Precio/mes | SLA Uptime | Soporte |
|---|---|---|---|---|
| Starter | 10.000 | 890 EUR | 99.5% | Email (48h) |
| Business | 50.000 | 2.500 EUR | 99.9% | Email + Chat (8h) |
| Enterprise | 250.000 | 8.200 EUR | 99.99% | 24/7 Dedicado |
| Unlimited | Sin límite | Precio a medida | 99.99% + SLA contractual | Account Manager dedicado |

### SLA Técnico
- Tiempo de respuesta API (P95): < 1.2 segundos
- Tiempo de respuesta en caché semántica: < 50 ms
- RPO (Recovery Point Objective): 1 hora
- RTO (Recovery Time Objective): 15 minutos
- Ventanas de mantenimiento: Domingos 02:00-04:00 UTC (notificación 72h previas)

---

## PRODUCTO 2: GraphRAG Enterprise Engine

### Descripción Ejecutiva
Motor de búsqueda semántica de nível profundo que conecta datos no estructurados (PDFs, emails, contratos, notas de reunión) con un grafo de conocimiento Neo4j para permitir respuestas que razonan sobre relaciones entre entidades, no solo sobre similitud textual.

### Arquitectura Técnica
- **Extracción de Entidades**: LLM (GPT-4o) + spaCy (modelo es_core_news_lg) + reglas personalizadas
- **Grafo de Conocimiento**: Neo4j AuraDB con esquema ontológico personalizable
- **Vector Store Primario**: ChromaDB con HNSW index (cosine similarity)
- **Vector Store Alternativo**: Pinecone (para clientes con >10M documentos)
- **Búsqueda Híbrida**: Fusión BM25 (léxica) + Embeddings (semántica) + Traversal Grafo (estructural)
- **Reranking**: Cohere Rerank API (cross-encoder)
- **Algoritmos de Grafo**: PageRank (importancia de nodos), Louvain (clustering de comunidades), A* (búsqueda de caminos)
- **Caché Semántica**: ChromaDB (umbral cosine similarity > 0.92)

### Componentes del Sistema

#### Motor de Ingesta
1. Extracción de texto: PyPDF2, pdfplumber (PDF), Tesseract OCR (imágenes escaneadas)
2. Chunking inteligente: RecursiveCharacterTextSplitter con overlap 20%
3. Extracción NER: Pipeline LLM con prompt de extracción de entidades y relaciones
4. UPSERT Neo4j: Driver Cypher con MERGE para evitar duplicados
5. UPSERT ChromaDB: Colección `document_store` con metadata del documento fuente

#### Motor de Consulta
1. Guardrails de entrada: PromptGuard + reglas de regex (prompt injection, jailbreak)
2. Extracción de entidades de la query: LLM identifica entidades clave en la pregunta
3. Búsqueda vectorial: ChromaDB top-K=5 (cosine similarity)
4. Traversal de grafo: Neo4j Cypher 2-hop neighborhood de las entidades detectadas
5. Reranking: Cohere Rerank sobre el contexto unificado
6. Síntesis: LLM con contexto enriquecido (Graph + Vector) genera respuesta final

### Formatos de Documentos Soportados
- Texto: .txt, .md, .rst, .csv
- Documentos ofimáticos: .pdf, .docx, .xlsx, .pptx
- Correo electrónico: .eml, .msg (via integración Exchange/Gmail)
- Imágenes con texto: .png, .jpg, .tiff (via OCR)
- Datos estructurados: JSON, XML, YAML (ingestados como entidades de grafo)

### Precios y Licencias
- **Setup / Onboarding**: 4.800 EUR (único pago — incluye 40h de consultoría de implementación)
- **Mantenimiento Mensual (Basic)**: 1.200 EUR/mes (hasta 1M documentos, 1 instancia Neo4j)
- **Mantenimiento Mensual (Advanced)**: 2.800 EUR/mes (hasta 10M documentos, 3 instancias Neo4j + HA)
- **Mantenimiento Mensual (Ultimate)**: Precio a medida (multi-tenant, SLA 99.99%)

---

## PRODUCTO 3: Vision-LLM Document Automation

### Descripción Ejecutiva
Sistema de extracción inteligente de información de documentos visuales (facturas, contratos PDF escaneados, formularios, albaranes) combinando modelos de visión multimodal con LLMs de texto para transformar documentos no estructurados en datos estructurados listos para ERP o base de datos.

### Tecnologías Empleadas
- **Modelo de Visión**: LLaVA-1.6 (Microsoft), GPT-4V (OpenAI), Gemini 1.5 Pro (Google) — configurable
- **OCR de respaldo**: Tesseract 5.0 + AWS Textract (para documentos complejos)
- **Post-procesamiento**: Reglas de validación + LLM para corrección de errores OCR
- **Formato de salida**: JSON estructurado, XML, CSV, entrada directa en ERP vía API

### Integraciones ERP/Contabilidad
- SAP S/4HANA (via SAP Business Connector)
- ERPNEXT (via REST API)
- Sage 50 / Sage 200 (via módulo de importación CSV)
- Holded (via API nativa)
- QuickBooks (via OAuth2 API)

### Integraciones de Workflow
- Make (Integromat): Módulo personalizado NexusAI disponible en Marketplace
- n8n: Node custom desarrollado por NexusAI (open source, repositorio GitHub)
- Zapier: Integración via Webhooks genéricos

### Precios
- **Plan Básico (hasta 500 documentos/mes)**: 890 EUR/mes
- **Plan Professional (hasta 5.000 documentos/mes)**: 1.800 EUR/mes
- **Plan Enterprise (documentos ilimitados)**: 4.200 EUR/mes + soporte dedicado

---

## PRODUCTO 4: LLM Security Audit & Guardrails Suite

### Descripción Ejecutiva
Servicio de auditoría y hardening de sistemas de IA generativa para empresas que ya tienen LLMs desplegados o quieren proteger nuevas implementaciones. Cubre amenazas específicas del ecosistema LLM: prompt injection, jailbreaking, exfiltración de datos vía prompts, alucinaciones, sesgos sistemáticos.

### Tipos de Auditoría

#### Auditoría Red Team LLM (5 días)
- 200+ ataques de prompt injection automatizados
- 50+ escenarios de jailbreak manual (técnicas DAN, PAIR, Tree of Attacks)
- Pruebas de exfiltración de system prompt
- Detección de sesgos en respuestas (género, etnia, geografía)
- **Entregable**: Informe ejecutivo + recomendaciones técnicas priorizadas por criticidad
- **Precio**: 3.000 EUR por evaluación

#### Implementación de Guardrails Continuos (Servicio Mensual)
- Capa NeMo Guardrails (NVIDIA) configurada según política del cliente
- Monitor de alucinaciones con SelfCheckGPT
- Clasificador de toxicidad (ModeratedContent API de OpenAI)
- Dashboard de incidencias en tiempo real
- **Precio**: 1.500 EUR/mes

### Clientes que usan LLM Security Audit
- BancoCentral Hispano: Auditoría Red Team semestral (2 veces/año)
- Pharma Nova S.A.: Guardrails continuos para sistema RAG de ensayos clínicos
- Despacho Martínez & Asociados: Auditoría puntual previa al despliegue del sistema legal

---

## PRODUCTO 5: NexusAI Platform (SaaS Multi-tenant) — EN DESARROLLO

### Descripción
Plataforma SaaS unificada que consolidará todos los productos anteriores en un único dashboard. Los clientes podrán crear y gestionar sus propios agentes RAG, grafos de conocimiento, pipelines de ingesta y monitorear métricas desde una interfaz web sin necesidad de soporte técnico.

### Estado del Desarrollo
- **Fase actual**: Alpha Privada (acceso a 5 clientes beta selectos)
- **Clientes Beta**: BancoCentral Hispano, MegaRetail Iberia, Pharma Nova, Tiendas Verde Orgánico, Despacho Martínez
- **Fecha estimada de lanzamiento GA**: Q2 2027
- **Responsable de Producto**: Diego Ríos (CPO)
- **Responsable Técnico**: Mateo Benítez (Engineering Lead)
- **Stack Frontend**: React 19 + Vite, Three.js para grafo 3D, Tailwind CSS
- **Stack Backend**: FastAPI + LangGraph + Neo4j + ChromaDB (arquitectura actual)
- **Inversión estimada en desarrollo**: 1.200.000 EUR (cubierta por la Serie B)

---

## ROADMAP DE PRODUCTO 2026-2027

| Trimestre | Hito | Responsable |
|---|---|---|
| Q3 2026 | Lanzamiento soporte multi-idioma (8 idiomas) en Customer360 | Lena Fischer + Dr. Yuki Tanaka |
| Q3 2026 | Integración nativa con Microsoft Teams para Customer360 | Pablo Ruiz |
| Q4 2026 | SOC 2 Type II certificación completada | Sofía Alarcón |
| Q4 2026 | GraphRAG Engine v2.0: GNN (Graph Neural Networks) para embeddings de grafo | Ahmed Al-Rashid |
| Q1 2027 | NexusAI Platform Alpha → Beta Pública | Diego Ríos |
| Q1 2027 | Expansión a Brasil — Apertura oficina São Paulo | Andrés Castro |
| Q2 2027 | NexusAI Platform GA (General Availability) | Carlos Mendoza |
| Q2 2027 | Lanzamiento vertical NexusAI Education | Elena Rostova |
| Q3 2027 | IPO preparatoria (exploración con bancos de inversión) | Valentina Cruz |
