import math
import re
from typing import List, Dict, Any, Tuple


class OkapiBM25:
    """
    Motor de búsqueda léxica BM25 (Okapi BM25) puro en Python.
    Optimizado para recuperación de términos exactos, códigos alfanuméricos,
    nombres propios, CIFs, identificadores de contratos e incidencias.

    Soporta indexación incremental: add_documents() y remove_documents_by_prefix()
    permiten actualizar el índice sin reconstruirlo completo desde cero.
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75, epsilon: float = 0.25):
        self.k1 = k1
        self.b = b
        self.epsilon = epsilon
        self.corpus_size = 0
        self.avg_doc_len = 0.0
        self.doc_lengths: List[int] = []
        self.doc_token_freqs: List[Dict[str, int]] = []
        self.doc_metadata: List[Dict[str, Any]] = []
        self.idf: Dict[str, float] = {}
        # Estado incremental: frecuencia de documentos por término y mapa de IDs
        self._df: Dict[str, int] = {}
        self._total_token_count: int = 0
        self._doc_id_to_idx: Dict[str, int] = {}

    SPANISH_STOPWORDS = {
        "de", "la", "el", "en", "es", "para", "por", "que", "un", "una",
        "los", "las", "del", "al", "con", "se", "lo", "como", "su", "sus",
        "cual", "cuál", "que", "qué", "sobre", "entre", "este", "esta",
        "estos", "estas", "son", "fue", "era", "ha", "han", "hay", "tiene",
        "tienen", "ser", "estar", "hacer", "año", "años", "año?"
    }

    def _tokenize(self, text: str) -> List[str]:
        """
        Tokenizador adaptado para textos empresariales en español:
        - Convierte a minúsculas.
        - Preserva identificadores alfanuméricos con guiones (ej. CNT-2023-MRI-008, INC-2026-001).
        - Elimina signos de puntuación periféricos manteniendo acentos y caracteres útiles.
        - Filtra stopwords gramaticales comunes salvo que contengan dígitos o guiones.
        """
        if not text:
            return []
        
        # Extraer tokens preservando guiones internos en códigos alfanuméricos
        raw_tokens = re.findall(r'[a-zA-Z0-9áéíóúÁÉÍÓÚñÑ]+(?:-[a-zA-Z0-9áéíóúÁÉÍÓÚñÑ]+)*', text.lower())
        tokens = []
        for t in raw_tokens:
            if len(t) > 1:
                # Si contiene dígitos o guiones, es un identificador crítico y nunca se filtra
                if any(c.isdigit() for c in t) or "-" in t:
                    tokens.append(t)
                elif t not in self.SPANISH_STOPWORDS:
                    tokens.append(t)
        return tokens

    def _recalculate_idf(self):
        """Recalcula el IDF completo a partir del estado incremental _df."""
        self.idf = {}
        negative_idfs = []
        for term, freq in self._df.items():
            idf_val = math.log((self.corpus_size - freq + 0.5) / (freq + 0.5) + 1.0)
            if idf_val < 0:
                negative_idfs.append((term, idf_val))
            else:
                self.idf[term] = idf_val

        if self.idf:
            avg_idf = sum(self.idf.values()) / len(self.idf)
            eps_idf = self.epsilon * avg_idf
            for term, _ in negative_idfs:
                self.idf[term] = eps_idf

    def _update_avg_doc_len(self):
        """Recalcula la longitud media de documentos."""
        self.avg_doc_len = self._total_token_count / self.corpus_size if self.corpus_size > 0 else 0.0

    def add_documents(self, documents: List[Dict[str, Any]]):
        """
        Añade documentos al índice BM25 de forma incremental.
        Solo procesa los documentos nuevos y recalcula el IDF global.
        Si un doc_id ya existe, se omite (upsert = no duplicar).

        Complejidad: O(k * avg_tokens) donde k = documentos nuevos.
        """
        if not documents:
            return

        new_count = 0
        for doc in documents:
            doc_id = doc.get("doc_id", "")
            text = doc.get("text", "")
            meta = doc.get("metadata", {})

            # Deduplicar: si ya existe este doc_id, saltar
            if doc_id in self._doc_id_to_idx:
                continue

            tokens = self._tokenize(text)
            doc_len = len(tokens)

            # Registrar en las estructuras internas
            idx = len(self.doc_metadata)
            self._doc_id_to_idx[doc_id] = idx
            self.doc_lengths.append(doc_len)
            self._total_token_count += doc_len

            # Conteo de frecuencia de términos en este documento
            tf: Dict[str, int] = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            self.doc_token_freqs.append(tf)

            # Actualizar frecuencia de documentos (DF) incremental
            for t in tf.keys():
                self._df[t] = self._df.get(t, 0) + 1

            self.doc_metadata.append({
                "doc_id": doc_id,
                "text": text,
                "metadata": meta
            })
            new_count += 1

        if new_count > 0:
            self.corpus_size += new_count
            self._update_avg_doc_len()
            self._recalculate_idf()

    def remove_documents_by_ids(self, doc_ids: List[str]) -> int:
        """
        Elimina del índice BM25 los documentos cuyos IDs exactos estén en la lista.
        Recalcula IDF y avg_doc_len tras la eliminación.

        :param doc_ids: Lista de IDs exactos a eliminar.
        :return: Número de documentos eliminados.
        """
        if not doc_ids:
            return 0

        doc_ids_set = set(doc_ids)
        indices_to_remove = set()

        for doc_id, idx in self._doc_id_to_idx.items():
            if doc_id in doc_ids_set:
                indices_to_remove.add(idx)

        if not indices_to_remove:
            return 0

        # Decrementar DF para los términos de los documentos eliminados
        for idx in indices_to_remove:
            tf = self.doc_token_freqs[idx]
            for term in tf.keys():
                self._df[term] = self._df.get(term, 1) - 1
                if self._df[term] <= 0:
                    del self._df[term]
            self._total_token_count -= self.doc_lengths[idx]

        # Reconstruir las listas compactas sin los índices eliminados
        new_lengths = []
        new_freqs = []
        new_metadata = []
        new_id_map = {}

        for old_idx in range(len(self.doc_metadata)):
            if old_idx in indices_to_remove:
                continue
            new_idx = len(new_metadata)
            new_lengths.append(self.doc_lengths[old_idx])
            new_freqs.append(self.doc_token_freqs[old_idx])
            new_metadata.append(self.doc_metadata[old_idx])
            new_id_map[self.doc_metadata[old_idx]["doc_id"]] = new_idx

        self.doc_lengths = new_lengths
        self.doc_token_freqs = new_freqs
        self.doc_metadata = new_metadata
        self._doc_id_to_idx = new_id_map
        self.corpus_size = len(new_metadata)
        self._update_avg_doc_len()
        self._recalculate_idf()

        return len(indices_to_remove)

    def search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """
        Evalúa la consulta contra el índice BM25 y retorna los mejores candidatos ordenados.
        """
        if self.corpus_size == 0:
            return []

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scores: List[Tuple[int, float]] = []

        for idx, (tf, doc_len) in enumerate(zip(self.doc_token_freqs, self.doc_lengths)):
            score = 0.0
            len_norm = 1.0 - self.b + self.b * (doc_len / self.avg_doc_len if self.avg_doc_len > 0 else 1.0)

            for token in query_tokens:
                if token not in tf:
                    continue
                term_freq = tf[token]
                term_idf = self.idf.get(token, 0.0)

                # Fórmula de saturación de frecuencia BM25
                num = term_freq * (self.k1 + 1.0)
                denom = term_freq + self.k1 * len_norm
                score += term_idf * (num / denom)

            if score > 0.0:
                scores.append((idx, round(score, 4)))

        # Ordenar por score decreciente
        scores.sort(key=lambda x: x[1], reverse=True)

        results = []
        for rank, (doc_idx, bm25_score) in enumerate(scores[:top_k], start=1):
            doc_data = self.doc_metadata[doc_idx]
            results.append({
                "doc_id": doc_data["doc_id"],
                "text": doc_data["text"],
                "metadata": doc_data["metadata"],
                "bm25_score": bm25_score,
                "bm25_rank": rank
            })

        return results


def reciprocal_rank_fusion(
    vector_results: List[Dict[str, Any]],
    bm25_results: List[Dict[str, Any]],
    k: int = 60,
    top_k: int = 5
) -> List[Dict[str, Any]]:
    """
    Implementa Reciprocal Rank Fusion (RRF) explícito y auditable.
    Combina los rankings de búsqueda léxica (BM25) y búsqueda vectorial (ChromaDB):
    
    RRF(d) = sum( 1 / (k + rank_i(d)) )
    
    :param vector_results: Lista de chunks ordenados por similitud vectorial.
    :param bm25_results: Lista de chunks ordenados por score BM25.
    :param k: Constante de suavizado RRF (default 60).
    :param top_k: Cantidad de candidatos finales a retornar.
    :return: Lista de candidatos enriquecidos con métricas de procedencia y scores auditables.
    """
    candidate_map: Dict[str, Dict[str, Any]] = {}

    # 1. Procesar ranking vectorial
    for rank, item in enumerate(vector_results, start=1):
        # Usar doc_id o snippet de texto como clave única del chunk
        doc_id = item.get("doc_id") or item.get("chunk_id") or item.get("metadata", {}).get("chunk_index")
        key = str(doc_id) if doc_id is not None else item["text"][:100]

        candidate_map[key] = {
            "key": key,
            "doc_id": doc_id or key,
            "text": item.get("text", ""),
            "metadata": item.get("metadata", {}),
            "vector_score": float(item.get("similarity", 0.0)),
            "vector_rank": rank,
            "bm25_score": 0.0,
            "bm25_rank": None,
            "rrf_score": 1.0 / (k + rank),
            "origin_channels": ["vector"]
        }

    # 2. Procesar ranking BM25
    for rank, item in enumerate(bm25_results, start=1):
        doc_id = item.get("doc_id") or item.get("chunk_id") or item.get("metadata", {}).get("chunk_index")
        key = str(doc_id) if doc_id is not None else item["text"][:100]

        rrf_contribution = 1.0 / (k + rank)

        if key in candidate_map:
            candidate_map[key]["bm25_score"] = float(item.get("bm25_score", 0.0))
            candidate_map[key]["bm25_rank"] = rank
            candidate_map[key]["rrf_score"] += rrf_contribution
            if "bm25" not in candidate_map[key]["origin_channels"]:
                candidate_map[key]["origin_channels"].append("bm25")
        else:
            candidate_map[key] = {
                "key": key,
                "doc_id": doc_id or key,
                "text": item.get("text", ""),
                "metadata": item.get("metadata", {}),
                "vector_score": 0.0,
                "vector_rank": None,
                "bm25_score": float(item.get("bm25_score", 0.0)),
                "bm25_rank": rank,
                "rrf_score": rrf_contribution,
                "origin_channels": ["bm25"]
            }

    # 3. Ordenar candidatos consolidados por RRF Score descendente
    fused_candidates = list(candidate_map.values())
    fused_candidates.sort(key=lambda x: x["rrf_score"], reverse=True)

    # 4. Asignar final_rank
    for final_rank, cand in enumerate(fused_candidates[:top_k], start=1):
        cand["final_rank"] = final_rank
        cand["rrf_score"] = round(cand["rrf_score"], 6)

    return fused_candidates[:top_k]
