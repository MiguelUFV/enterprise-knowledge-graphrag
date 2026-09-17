import re
from typing import List, Dict, Any, Optional


class StructuredChunk:
    """
    Representa un fragmento de documento enriquecido con metadatos contextuales.
    """
    def __init__(
        self,
        chunk_id: str,
        text: str,
        filename: str,
        chunk_index: int,
        header_path: str = "",
        metadata: Optional[Dict[str, Any]] = None
    ):
        self.chunk_id = chunk_id
        self.text = text
        self.filename = filename
        self.chunk_index = chunk_index
        self.header_path = header_path
        self.metadata = metadata or {}

    def to_dict(self) -> Dict[str, Any]:
        meta = {
            "filename": self.filename,
            "chunk_index": self.chunk_index,
            "header_path": self.header_path,
            "char_count": len(self.text),
            **self.metadata
        }
        return {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "metadata": meta
        }


class StructuredChunker:
    """
    Segmentador semántico y estructurado de documentos para RAG de alta precisión.
    - Preserva intactas las tablas Markdown (|...|...|).
    - Preserva bloques de código (```...```) y listas completas.
    - Mantiene el rastro jerárquico de títulos (# Encabezado > ## Subtítulo) como prefijo contextual.
    - Segmenta en límites de oraciones respetando tamaño objetivo y solapamiento.
    """

    def __init__(
        self,
        target_chunk_size: int = 800,
        chunk_overlap: int = 150,
        min_chunk_size: int = 60
    ):
        self.target_chunk_size = target_chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_size = min_chunk_size

    def _is_table_row(self, line: str) -> bool:
        stripped = line.strip()
        return stripped.startswith("|") and stripped.endswith("|")

    def _is_header(self, line: str) -> Optional[tuple[int, str]]:
        stripped = line.strip()
        match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
        if match:
            return (len(match.group(1)), match.group(2).strip())
        return None

    def chunk_document(self, content: str, filename: str) -> List[StructuredChunk]:
        """
        Segmenta el contenido de un documento respetando su estructura y jerarquía.
        """
        lines = content.splitlines()
        sections: List[Dict[str, Any]] = []

        current_header_stack: List[str] = []
        current_block_lines: List[str] = []
        in_code_block = False
        in_table = False
        current_table_lines: List[str] = []

        def flush_block(header_path: str):
            nonlocal current_block_lines
            if current_block_lines:
                raw_text = "\n".join(current_block_lines).strip()
                if raw_text:
                    sections.append({
                        "header_path": header_path,
                        "text": raw_text,
                        "is_atomic": False
                    })
                current_block_lines = []

        def flush_table(header_path: str):
            nonlocal current_table_lines, in_table
            if current_table_lines:
                table_text = "\n".join(current_table_lines).strip()
                if table_text:
                    # 1. Conservar la tabla completa como unidad atómica
                    sections.append({
                        "header_path": header_path,
                        "text": table_text,
                        "is_atomic": True
                    })
                    # 2. Si la tabla es extensa (> 4 líneas), generar también subsecciones por fila con cabecera
                    if len(current_table_lines) > 4:
                        hdr_line = current_table_lines[0]
                        sep_line = current_table_lines[1] if len(current_table_lines) > 1 else ""
                        for r_idx, row_line in enumerate(current_table_lines[2:], start=1):
                            if row_line.strip() and self._is_table_row(row_line):
                                row_chunk_text = f"{hdr_line}\n{sep_line}\n{row_line}"
                                sections.append({
                                    "header_path": f"{header_path} > Fila {r_idx}" if header_path else f"Tabla > Fila {r_idx}",
                                    "text": row_chunk_text,
                                    "is_atomic": True
                                })
                current_table_lines = []
            in_table = False

        for line in lines:
            stripped = line.strip()

            # Detección de bloques de código
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                current_block_lines.append(line)
                continue

            if in_code_block:
                current_block_lines.append(line)
                continue

            # Detección de tablas
            if self._is_table_row(line):
                if not in_table:
                    flush_block(" > ".join(current_header_stack))
                    in_table = True
                current_table_lines.append(line)
                continue
            elif in_table:
                flush_table(" > ".join(current_header_stack))

            # Detección de encabezados Markdown (#, ##, ###)
            header_info = self._is_header(line)
            if header_info:
                level, title = header_info
                flush_block(" > ".join(current_header_stack))
                
                # Ajustar la pila de encabezados según nivel
                while len(current_header_stack) >= level:
                    current_header_stack.pop()
                current_header_stack.append(title)
                continue

            # Separadores horizontales (---, ===)
            if re.match(r'^[=\-_*]{3,}\s*$', stripped):
                flush_block(" > ".join(current_header_stack))
                continue

            current_block_lines.append(line)

        # Vaciar pendientes
        if in_table:
            flush_table(" > ".join(current_header_stack))
        flush_block(" > ".join(current_header_stack))

        # Generar los chunks finales a partir de las secciones
        chunks: List[StructuredChunk] = []
        chunk_counter = 0

        for sec in sections:
            header_path = sec["header_path"]
            sec_text = sec["text"]
            is_atomic = sec["is_atomic"]

            # Si es atómico (ej. tabla) o suficientemente compacto
            if is_atomic or len(sec_text) <= self.target_chunk_size:
                if len(sec_text) >= self.min_chunk_size:
                    header_prefix = f"[{header_path}]\n" if header_path else ""
                    chunk_text = f"{header_prefix}{sec_text}"
                    chunk_id = f"{filename}_chunk_{chunk_counter}"
                    chunks.append(
                        StructuredChunk(
                            chunk_id=chunk_id,
                            text=chunk_text,
                            filename=filename,
                            chunk_index=chunk_counter,
                            header_path=header_path,
                            metadata={"is_table": is_atomic}
                        )
                    )
                    chunk_counter += 1
                continue

            # Si la sección es más larga, dividir por oraciones y párrafos
            sub_chunks = self._split_text_with_overlap(sec_text, self.target_chunk_size, self.chunk_overlap)
            for sub_text in sub_chunks:
                if len(sub_text) >= self.min_chunk_size:
                    header_prefix = f"[{header_path}]\n" if header_path else ""
                    chunk_text = f"{header_prefix}{sub_text}"
                    chunk_id = f"{filename}_chunk_{chunk_counter}"
                    chunks.append(
                        StructuredChunk(
                            chunk_id=chunk_id,
                            text=chunk_text,
                            filename=filename,
                            chunk_index=chunk_counter,
                            header_path=header_path,
                            metadata={"is_table": False}
                        )
                    )
                    chunk_counter += 1

        return chunks

    def _split_text_with_overlap(self, text: str, max_size: int, overlap: int) -> List[str]:
        """
        Divide un texto largo en límites de oraciones respetando max_size y overlap.
        """
        # Separar por párrafos primero
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        result_chunks: List[str] = []
        current_chunk = ""

        for para in paragraphs:
            if len(current_chunk) + len(para) + 2 <= max_size:
                current_chunk = f"{current_chunk}\n\n{para}".strip() if current_chunk else para
            else:
                if current_chunk:
                    result_chunks.append(current_chunk)
                    # Mantener solapamiento de las últimas oraciones
                    sentences = re.split(r'(?<=[.?!])\s+', current_chunk)
                    overlap_acc = ""
                    for s in reversed(sentences):
                        if len(overlap_acc) + len(s) < overlap:
                            overlap_acc = f"{s} {overlap_acc}".strip()
                        else:
                            break
                    current_chunk = f"{overlap_acc}\n\n{para}".strip() if overlap_acc else para
                else:
                    # El párrafo individual excede max_size, dividir por oraciones
                    sentences = re.split(r'(?<=[.?!])\s+', para)
                    for sentence in sentences:
                        if len(current_chunk) + len(sentence) + 1 <= max_size:
                            current_chunk = f"{current_chunk} {sentence}".strip() if current_chunk else sentence
                        else:
                            if current_chunk:
                                result_chunks.append(current_chunk)
                            current_chunk = sentence

        if current_chunk and (not result_chunks or current_chunk != result_chunks[-1]):
            result_chunks.append(current_chunk)

        return result_chunks


# Instancia singleton predeterminada
structured_chunker = StructuredChunker()
