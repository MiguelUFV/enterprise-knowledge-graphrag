import os
import re
import json
import time
import logging
from typing import List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

RERANKING_ENABLED = os.getenv("RERANKING_ENABLED", "true").lower() in ("true", "1", "yes")
RERANKER_ENGINE = os.getenv("RERANKER_ENGINE", "local").lower() # "local" (ONNX/FlashRank), "llm", "auto"
LOCAL_RERANKER_MODEL = os.getenv("LOCAL_RERANKER_MODEL", "ms-marco-MiniLM-L-12-v2")
PRIMARY_MODEL = os.getenv("PRIMARY_MODEL", "openrouter/openai/gpt-4o-mini")
OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")

RERANK_PROMPT = """Eres un sistema de Re-ranking Cross-Encoder de alta precisión para un motor de Information Retrieval.
Tu objetivo es evaluar la relevancia estricta de una lista de fragmentos candidatos respecto a la consulta del usuario.

Consulta del usuario:
"{query}"

Fragmentos candidatos:
{candidates_json}

INSTRUCCIONES DE CALIFICACIÓN:
1. Para cada fragmento, asigna un 'relevance_score' entre 0.00 y 1.00:
   - 1.00: Responde directamente a la pregunta con datos exactos o resuelve la necesidad de información.
   - 0.70 - 0.90: Muy relevante, aporta contexto esencial o hechos directamente vinculados.
   - 0.40 - 0.60: Marginalmente relevante (menciona términos similares pero no responde el núcleo).
   - 0.00 - 0.30: Irrelevante o ruido.
2. Devuelve ÚNICAMENTE un JSON válido con la siguiente estructura:
{{
  "rankings": [
    {{
      "candidate_index": 0,
      "relevance_score": 0.95,
      "relevance_rationale": "Breve justificación de 1 frase"
    }}
  ]
}}
"""


class CrossEncoderReranker:
    """
    Motor de Re-ranking Cross-Encoder Local (ONNX / FlashRank) con Fallback a LLM y RRF.
    Ejecuta re-ranking de atención cruzada 100% en local en CPU (<50ms) sin depender de APIs
    externas ni generar costes por token.
    """

    def __init__(
        self,
        enabled: bool = RERANKING_ENABLED,
        engine: str = RERANKER_ENGINE,
        local_model: str = LOCAL_RERANKER_MODEL,
        llm_model: str = PRIMARY_MODEL
    ):
        self.enabled = enabled
        self.engine = engine
        self.local_model_name = local_model
        self.llm_model = llm_model
        self._local_ranker = None
        self._local_ranker_failed = False
        self._consecutive_network_errors = 0
        self._circuit_broken_until = 0.0

        if self.enabled and self.engine in ("local", "auto"):
            self._init_local_ranker()

    def _init_local_ranker(self):
        """Inicializa el modelo local ONNX de FlashRank."""
        try:
            from flashrank import Ranker
            logger.info(f"Cargando modelo local ONNX de Re-ranking '{self.local_model_name}'...")
            self._local_ranker = Ranker(model_name=self.local_model_name)
            logger.info(f"Modelo local ONNX '{self.local_model_name}' cargado con éxito en CPU.")
        except Exception as e:
            logger.warning(f"No se pudo inicializar FlashRank local ({e}). Fallback a modo LLM/RRF.")
            self._local_ranker_failed = True

    def should_rerank(self, query: str, candidates: List[Dict[str, Any]]) -> Tuple[bool, str]:
        """
        Determina si una consulta amerita el reordenamiento.
        Evita cómputo innecesario en consultas con match exacto o candidato RRF dominante.
        """
        if not self.enabled:
            return False, "RERANKER_DISABLED"
        if not candidates or len(candidates) < 2:
            return False, "INSUFFICIENT_CANDIDATES"

        # Con el modelo local ONNX (<50 ms) los atajos no ahorran nada y en cambio
        # dejan sin puntuación de relevancia al clasificador epistémico.
        if self._local_ranker is not None and not self._local_ranker_failed:
            return True, "EXECUTE_RERANKING_LOCAL"

        # 1. Bypass por Código / Identificador Empresarial Exacto
        exact_code_pattern = r'\b(cnt-\d+|inc-\d+|cif\s+[a-z0-9-]+|iso-\d+|soc\s+2)\b'
        if re.search(exact_code_pattern, query.lower()):
            return False, "BYPASS_EXACT_IDENTIFIER"

        # 2. Bypass por Candidato RRF Dominante
        c0_score = candidates[0].get("rrf_score", 0.0)
        c1_score = candidates[1].get("rrf_score", 0.0) if len(candidates) > 1 else 0.0
        if c0_score >= 0.032 and (c0_score - c1_score) >= 0.008:
            return False, "BYPASS_DOMINANT_RRF"

        return True, "EXECUTE_RERANKING"

    def _rerank_local(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Ejecuta el re-ranking local ultra-rápido en CPU usando FlashRank (ONNX).
        """
        from flashrank import RerankRequest

        active_pool = candidates[:10]  # Evaluar hasta los mejores 10 candidatos
        passages = [
            {"id": idx, "text": c.get("text", "")[:500]}
            for idx, c in enumerate(active_pool)
        ]

        rerank_request = RerankRequest(query=query, passages=passages)
        results = self._local_ranker.rerank(rerank_request)

        # Mapear scores y normalizar
        reranked_list = []
        for rank, res in enumerate(results, start=1):
            orig_idx = res["id"]
            cand_copy = dict(active_pool[orig_idx])
            raw_score = float(res.get("score", 0.0))
            cand_copy["reranker_score"] = round(raw_score, 4)
            cand_copy["reranker_rank"] = rank
            cand_copy["rerank_rationale"] = f"Local ONNX FlashRank ({self.local_model_name})"
            reranked_list.append(cand_copy)

        return reranked_list[:top_k]

    def _rerank_llm(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Re-ranking mediante LLM estructurado (Fallback o modo cloud).
        """
        import litellm

        if time.time() < self._circuit_broken_until:
            raise RuntimeError("Circuit breaker activo")

        active_pool = candidates[:6]
        payload = [
            {
                "candidate_index": idx,
                "doc_id": str(c.get("doc_id", idx)),
                "snippet": c.get("text", "")[:350]
            }
            for idx, c in enumerate(active_pool)
        ]

        user_content = RERANK_PROMPT.format(
            query=query,
            candidates_json=json.dumps(payload, ensure_ascii=False)
        )

        kwargs = {}
        if self.llm_model.startswith("openrouter/") and OPENROUTER_KEY:
            kwargs["api_key"] = OPENROUTER_KEY

        response = litellm.completion(
            model=self.llm_model,
            messages=[
                {"role": "system", "content": "Eres un evaluador de relevancia de información preciso y objetivo. Responde únicamente en JSON."},
                {"role": "user", "content": user_content}
            ],
            temperature=0.0,
            max_tokens=500,
            timeout=8.0,
            response_format={"type": "json_object"},
            **kwargs
        )

        raw_text = response.choices[0].message.content.strip()
        if "```" in raw_text:
            raw_text = re.sub(r"^```(?:json)?\s*", "", raw_text)
            raw_text = re.sub(r"\s*```$", "", raw_text)

        parsed = json.loads(raw_text)
        rankings = parsed.get("rankings", [])
        score_map = {r["candidate_index"]: float(r.get("relevance_score", 0.5)) for r in rankings if "candidate_index" in r}

        reranked_list = []
        for idx, cand in enumerate(active_pool):
            cand_copy = dict(cand)
            score = score_map.get(idx, 0.30)
            cand_copy["reranker_score"] = round(score, 4)
            cand_copy["rerank_rationale"] = next((r.get("relevance_rationale", "") for r in rankings if r.get("candidate_index") == idx), "LLM Evaluation")
            reranked_list.append(cand_copy)

        reranked_list.sort(key=lambda x: x["reranker_score"], reverse=True)
        for rank, item in enumerate(reranked_list[:top_k], start=1):
            item["reranker_rank"] = rank

        return reranked_list[:top_k]

    def rerank(
        self,
        query: str,
        candidates: List[Dict[str, Any]],
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Reordena la lista de candidatos con el motor local ONNX (prioritario) o LLM (fallback).
        Conserva todos los metadatos de procedencia (BM25, Vector, RRF).
        """
        if not candidates:
            return []

        # 1. Comprobar si amerita re-ranking (Bypass Heurístico)
        should_run, rationale = self.should_rerank(query, candidates)
        if not should_run:
            logger.info(f"Cross-Encoder adaptativo omitido ({rationale}) para '{query[:45]}...'.")
            res = []
            for r, c in enumerate(candidates[:top_k], start=1):
                c_copy = dict(c)
                c_copy["reranker_rank"] = r
                c_copy["reranker_score"] = round(c_copy.get("rrf_score", 0.0), 4)
                c_copy["rerank_rationale"] = f"Bypass adaptativo ({rationale})"
                res.append(c_copy)
            return res

        start_time = time.perf_counter()

        # 2. Intentar Re-ranking Local con FlashRank (ONNX)
        if self._local_ranker is not None and not self._local_ranker_failed:
            try:
                reranked = self._rerank_local(query, candidates, top_k=top_k)
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                logger.info(f"Re-ranking Local ONNX completado en {elapsed_ms:.2f}ms ({len(candidates)} candidatos -> Top {len(reranked)}).")
                return reranked
            except Exception as e:
                logger.warning(f"Fallo en Re-ranking Local ONNX ({e}). Intentando fallback a LLM...")

        # 3. Fallback a LLM si está configurado
        if self.engine in ("llm", "auto"):
            try:
                reranked = self._rerank_llm(query, candidates, top_k=top_k)
                elapsed_ms = (time.perf_counter() - start_time) * 1000
                self._consecutive_network_errors = 0
                logger.info(f"Re-ranking LLM fallback completado en {elapsed_ms:.2f}ms.")
                return reranked
            except Exception as e:
                logger.warning(f"Fallo en Re-ranking LLM ({e}). Fallback final a ranking RRF.")

        # 4. Fallback Seguro a RRF Ranking
        res = []
        for r, c in enumerate(candidates[:top_k], start=1):
            c_copy = dict(c)
            c_copy["reranker_rank"] = r
            c_copy["reranker_score"] = round(c_copy.get("rrf_score", 0.0), 4)
            c_copy["rerank_rationale"] = "Fallback RRF seguro"
            res.append(c_copy)
        return res


# Instancia singleton predeterminada
cross_encoder_reranker = CrossEncoderReranker()
