# MAPA DE RELACIONES INTER-ENTIDADES — NexusAI Solutions
## Documento de Referencia para Testing del HybridGraph RAG
### Generado: Agosto 2026

Este documento es una guía de referencia que consolida TODAS las relaciones entre entidades del corpus de NexusAI Solutions. Diseñado específicamente para validar que el sistema HybridGraph RAG pueda responder preguntas multi-salto (multi-hop).

---

## RELACIONES PERSONA → ORGANIZACIÓN

| Persona | Relación | Organización | Detalle |
|---|---|---|---|
| Elena Rostova | FUNDÓ / CEO DE | NexusAI Solutions S.L. | 2019, 34% accionariado |
| Carlos Mendoza | CO-FUNDÓ / CTO DE | NexusAI Solutions S.L. | 2019, 10% accionariado |
| Sofía Alarcón | CISO DE | NexusAI Solutions S.L. | Desde 2021 |
| Valentina Cruz | CFO DE | NexusAI Solutions S.L. | Desde 2020 |
| Diego Ríos | CPO DE | NexusAI Solutions S.L. | Desde 2022 |
| Mateo Benítez | Engineering Lead EN | NexusAI Solutions S.L. | Desde 2020 |
| Dr. Yuki Tanaka | Director Investigación EN | NexusAI Research Lab | Desde 2021 |
| Patricia Solano | Directora Legal EN | NexusAI Solutions + NexusAI Legal Tech | Dual rol |
| Dr. Andrei Volkov | Director EN | NexusAI Health S.L. | Subsidiaria 100% |
| James MacKenzie | CEO Socio EN | DataGraphX Inc. | Joint Venture 45% |
| Isabel Moreno | VP Sales EN | NexusAI Solutions | Desde 2022 |
| Marco Ferraro | Head of CS EN | NexusAI Solutions | Desde 2023 |
| Rafael Torres | KAM EN | NexusAI Solutions | Sector Financiero |
| Lucía Pardo | KAM EN | NexusAI Solutions | Sector Salud/Legal |
| Andrés Castro | BDM EN | NexusAI Solutions | Cobertura LATAM |
| Sophie Williams | BDM EN | NexusAI Solutions | Cobertura UK/Nordics |

---

## RELACIONES PERSONA → PERSONA (JERARQUÍA)

| Persona | Reporta A | Relación |
|---|---|---|
| Carlos Mendoza | Elena Rostova | CTO reporta a CEO |
| Sofía Alarcón | Elena Rostova | CISO reporta a CEO |
| Valentina Cruz | Elena Rostova | CFO reporta a CEO |
| Diego Ríos | Elena Rostova | CPO reporta a CEO |
| Dr. Yuki Tanaka | Elena Rostova | Research Director reporta a CEO |
| Patricia Solano | Elena Rostova | Legal Director reporta a CEO |
| Isabel Moreno | Elena Rostova | VP Sales reporta a CEO |
| Mateo Benítez | Carlos Mendoza | Engineering Lead reporta a CTO |
| Ana García | Carlos Mendoza | Data Engineer reporta a CTO |
| Javier Soto | Carlos Mendoza | Graph Engineer reporta a CTO |
| Lena Fischer | Carlos Mendoza | ML Engineer reporta a CTO |
| Pablo Ruiz | Carlos Mendoza | Backend Engineer reporta a CTO |
| Rodrigo Peña | Mateo Benítez | SRE reporta a Engineering Lead |
| Isabela Carvalho | Mateo Benítez | Cloud Architect reporta a Engineering Lead |
| Tomás Herrera | Mateo Benítez / Sofía Alarcón | Security Engineer (dual reporte) |
| Camila Vargas | Diego Ríos | Frontend Dev reporta a CPO |
| Nicolás Díaz | Diego Ríos | UX Designer reporta a CPO |
| Rafael Torres | Isabel Moreno | KAM reporta a VP Sales |
| Lucía Pardo | Isabel Moreno | KAM reporta a VP Sales |
| Andrés Castro | Isabel Moreno | BDM reporta a VP Sales |
| Sophie Williams | Isabel Moreno | BDM reporta a VP Sales |
| Marco Ferraro | Elena Rostova | CS Head reporta a CEO |
| Carolina Lima | Marco Ferraro | CSM reporta a CS Head |
| Sebastián Mora | Marco Ferraro | CSM reporta a CS Head |
| María José Fernández | Dr. Yuki Tanaka | Investigadora reporta a Research Director |
| Ahmed Al-Rashid | Dr. Yuki Tanaka | Investigador reporta a Research Director |
| Priya Sharma | Dr. Yuki Tanaka | Investigadora reporta a Research Director |
| Fernando Lagos | Patricia Solano | Abogado Junior reporta a Directora Legal |
| Nuria Blanco | Patricia Solano | DPO reporta a Directora Legal |

---

## RELACIONES CLIENTE → PRODUCTO → GESTOR

| Cliente | Producto Contratado | Gestor NexusAI | Contrato |
|---|---|---|---|
| BancoCentral Hispano | GraphRAG Enterprise Engine + LLM Security Audit | Rafael Torres | CNT-2021-BCP-001 |
| InvestGroup Capital | Customer360 (Business) | Rafael Torres | CNT-2022-IGC-004 |
| FinTech Nordica AB | Vision-LLM Document Automation | Sophie Williams | CNT-2023-FNA-007 |
| Pharma Nova S.A. | GraphRAG Enterprise (Advanced) + Guardrails | Lucía Pardo | CNT-2025-PN-011 |
| HospitalRed Mediterránea | NexusAI Health (PoC) | Lucía Pardo | CNT-2025-HRM-POC-001 |
| Despacho Martínez & Asociados | NexusAI Legal Tech | Lucía Pardo | CNT-2024-DMA-002 |
| LegalTech Global LLP | NexusAI Legal Tech (distribución UK) | Sophie Williams | CNT-2024-LTG-003 |
| MegaRetail Iberia | Customer360 (Unlimited) | Rafael Torres + Marco Ferraro | CNT-2023-MRI-008 |
| Tiendas Verde Orgánico | Customer360 (Starter) | Sebastián Mora | — |

---

## RELACIONES PRODUCTO → TECNOLOGÍA

| Producto | Tecnología | Tipo Relación |
|---|---|---|
| Customer360 Agent | LangGraph | ORQUESTADO_CON |
| Customer360 Agent | Claude 3.5 Sonnet | MODELO_PRINCIPAL |
| Customer360 Agent | GPT-4o | MODELO_ALTERNATIVO |
| Customer360 Agent | ChromaDB | VECTOR_STORE |
| Customer360 Agent | Neo4j AuraDB | GRAFO_CONOCIMIENTO |
| Customer360 Agent | Redis | SESIONES_CACHE |
| Customer360 Agent | PromptGuard | GUARDRAILS |
| Customer360 Agent | Salesforce | INTEGRA_CON |
| Customer360 Agent | HubSpot CRM | INTEGRA_CON |
| Customer360 Agent | WhatsApp Business API | INTEGRA_CON |
| Customer360 Agent | Zendesk | INTEGRA_CON |
| GraphRAG Engine | Neo4j AuraDB | GRAFO_CONOCIMIENTO |
| GraphRAG Engine | ChromaDB | VECTOR_STORE_PRINCIPAL |
| GraphRAG Engine | Pinecone | VECTOR_STORE_ALTERNATIVO |
| GraphRAG Engine | text-embedding-3-large | MODELO_EMBEDDINGS |
| GraphRAG Engine | Cohere Rerank | RERANKER |
| GraphRAG Engine | spaCy | NER_COMPLEMENTARIO |
| GraphRAG Engine | APOC | PLUGIN_NEO4J |
| GraphRAG Engine | GDS (Graph Data Science) | PLUGIN_NEO4J |
| Vision-LLM | LLaVA-1.6 | MODELO_VISION |
| Vision-LLM | GPT-4V | MODELO_VISION_PREMIUM |
| Vision-LLM | Gemini 1.5 Pro | MODELO_VISION_RECOMENDADO |
| Vision-LLM | Tesseract | OCR_BACKUP |
| Vision-LLM | SAP S/4HANA | INTEGRA_CON |
| Vision-LLM | Make/Integromat | INTEGRA_CON |
| LLM Security Audit | PromptGuard | HERRAMIENTA_PRINCIPAL |
| LLM Security Audit | NeMo Guardrails | GUARDRAILS_CONTINUOS |
| LLM Security Audit | SelfCheckGPT | DETECTOR_ALUCINACIONES |

---

## RELACIONES INCIDENTE → PERSONA → CLIENTE

| Incidente | Sistema Afectado | Detectado Por | Resuelto Por | Afecta A |
|---|---|---|---|---|
| INC-2026-001 | Customer360 Agent | Rodrigo Peña (Prometheus) | Sofía Alarcón + Tomás Herrera | BancoCentral Hispano |
| INC-2025-047 | Neo4j AuraDB | Rodrigo Peña (Prometheus) | Javier Soto + Carlos Mendoza | TODOS los clientes |
| INC-2026-003 | Customer360 (config) | Tiendas Verde Orgánico | Sebastián Mora | Tiendas Verde Orgánico |

---

## RELACIONES PROYECTO I+D → INVESTIGADORES → TECNOLOGÍAS

| Proyecto | Investigadores | Tecnologías Estudiadas | Resultado |
|---|---|---|---|
| NEXUS-RESEARCH-001 (Multimodal) | Dr. Yuki Tanaka, Priya Sharma, Lena Fischer | LLaVA-1.6, GPT-4V, Gemini 1.5 Pro, CLIP | En progreso - benchmark completado |
| NEXUS-RESEARCH-002 (GNN) | Ahmed Al-Rashid, Lena Fischer | PyTorch Geometric, GraphSAGE, GCP Vertex AI | Iniciado sept 2026 |
| NEXUS-RESEARCH-003 (RLHF) | Dr. Yuki Tanaka, María José Fernández | Llama 3.1 70B, PPO, RLHF | Investigación preliminar |
| NEXUS-RESEARCH-004 (Sesgos) | María José Fernández, Dr. Yuki Tanaka | RAGAS, análisis estadístico | COMPLETADO - ACL 2026 |

---

## RELACIONES INVERSORES → EMPRESA

| Inversor | Tipo | % | Ronda | Consejero |
|---|---|---|---|---|
| Elena Rostova | Fundadora | 34% | Pre-seed | Elena Rostova (CEO) |
| NorthStar Fund | Venture Capital | 22% | Seed+A+B | Alexandra Voss |
| Grupo Industrial Teyco S.A. | Estratégico | 15% | Serie A | Roberto Teyco |
| Carlos Mendoza | Co-Fundador | 10% | Pre-seed | Carlos Mendoza (CTO) |
| Business Angels Pool A | Varios | 11% | Pre-seed | Federico Blum (observador) |
| Empleados (Stock Options) | Colectivo | 8% | — | — |

---

## PREGUNTAS MULTI-HOP PARA TESTING DEL RAG

Las siguientes preguntas requieren razonar sobre 2+ saltos en el grafo:

1. **"¿Quién resolvió el incidente que afectó a BancoCentral Hispano?"** → INC-2026-001 → Sofía Alarcón + Tomás Herrera
2. **"¿Qué tecnología de embeddings usa el producto que gestiona Rafael Torres para su cliente más grande?"** → MegaRetail → Customer360 → text-embedding-3-large
3. **"¿Qué certificación necesita NexusAI para contratar con el Ministerio de Justicia?"** → OPP-2026-35 → ENS Nivel Alto
4. **"¿Quién está investigando los modelos que se usan en el producto más caro de NexusAI para procesar imágenes?"** → Vision-LLM → LLaVA/Gemini → Priya Sharma + Dr. Yuki Tanaka
5. **"¿Cuánto factura anualmente el cliente que tiene riesgo de no renovación?"** → MegaRetail → CNT-2023-MRI-008 → 180.000 EUR
6. **"¿Qué investigador publicó sobre sesgo en LLMs y también trabaja en el proyecto de RLHF?"** → NEXUS-RESEARCH-004 → María José Fernández → también en NEXUS-RESEARCH-003
7. **"¿Quién es el contacto del cliente cuya piloto termina en noviembre de 2026?"** → HospitalRed Mediterránea → Dra. Carmen Ruiz
8. **"¿Qué vulnerabilidad de seguridad afecta al sistema de ingesta de datos y quién es responsable de resolverla?"** → VULN-2026-08-B → /webhook/ingest → Pablo Ruiz + Sofía Alarcón
