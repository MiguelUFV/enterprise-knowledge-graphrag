"""
Módulo de Extracción Universal y Robusta de Documentos para GraphRAG.
Soporta PDF (con cascada de motores de tolerancia a fallos: PyMuPDF, pdfplumber, pypdf, pypdfium2),
DOCX, Markdown (.md), Texto plano (.txt), CSV y JSON.

Incluye normalización tipográfica, limpieza de caracteres nulos, detección de PDFs escaneados (sin OCR)
y multi-encoding adaptativo (UTF-8, Latin-1, CP1252, etc.).
"""

import io
import re
import logging
from dataclasses import dataclass, field
from typing import List, Dict, Any

logger = logging.getLogger("document_extractor")


@dataclass
class ExtractedDocument:
    filename: str
    text: str
    pages: List[str] = field(default_factory=list)
    page_count: int = 1
    engine_used: str = "plain_text"
    char_count: int = 0
    is_scanned_suspicion: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


class DocumentExtractor:
    """Extractor universal y tolerante a fallos para documentos empresariales."""

    @staticmethod
    def clean_extracted_text(text: str) -> str:
        """
        Limpia y normaliza el texto extraído:
        - Elimina bytes nulos y caracteres de control no imprimibles
        - Normaliza espacios Unicode (non-breaking spaces, zero-width spaces, soft hyphens)
        - Une palabras separadas por guión al final de línea (ej: 'infor-\\nmación' -> 'información')
        - Normaliza saltos de línea excesivos
        """
        if not text:
            return ""

        # Eliminar caracteres nulos
        text = text.replace("\x00", "")

        # Normalizar caracteres invisibles / especiales
        text = text.replace("\xa0", " ")      # Non-breaking space
        text = text.replace("\u200b", "")     # Zero-width space
        text = text.replace("\u200c", "")     # Zero-width non-joiner
        text = text.replace("\u200d", "")     # Zero-width joiner
        text = text.replace("\xad", "")       # Soft hyphen

        # Normalizar retornos de carro Windows/Mac a Unix
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # Unir palabras cortadas con guión al final de línea: letra- + \n + letra -> letraletra
        text = re.sub(r'([a-zA-ZáéíóúÁÉÍÓÚñÑ])-\n([a-zA-ZáéíóúÁÉÍÓÚñÑ])', r'\1\2', text)

        # Normalizar más de 2 saltos de línea consecutivos
        text = re.sub(r'\n{3,}', '\n\n', text)

        # Normalizar espacios en blanco repetidos en una misma línea
        text = re.sub(r'[ \t]{2,}', ' ', text)

        return text.strip()

    @classmethod
    def extract_from_bytes(cls, filename: str, raw_bytes: bytes) -> ExtractedDocument:
        """
        Extrae el texto de un buffer de bytes identificando la extensión del archivo.
        """
        if not raw_bytes:
            raise ValueError(f"El archivo '{filename}' está vacío (0 bytes).")

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

        if ext == "pdf":
            return cls._extract_pdf(filename, raw_bytes)
        elif ext in ("docx", "doc"):
            return cls._extract_docx(filename, raw_bytes)
        elif ext in ("txt", "md", "markdown", "csv", "json", "yaml", "yml", "log"):
            return cls._extract_text_file(filename, raw_bytes)
        else:
            # Intento genérico de texto plano con decodificador robusto
            logger.info(f"Extensión '.{ext}' no estándar; intentando decodificación de texto plano...")
            return cls._extract_text_file(filename, raw_bytes)

    @classmethod
    def _extract_pdf(cls, filename: str, raw_bytes: bytes) -> ExtractedDocument:
        """
        Cascada de motores de extracción para PDF con tolerancia total a fallos:
        1. PyMuPDF (pymupdf / fitz) - Máxima velocidad, precisión y manejo de layout
        2. pdfplumber - Excelente preservación de tablas y estructura
        3. pypdf - Parser en Python puro, tolerante con PDFs corruptos
        4. pypdfium2 - Motor PDFium
        5. pdfminer.six - Fallback de bajo nivel
        """
        pages_text: List[str] = []
        errors = []

        # --- MOTOR 1: PyMuPDF (pymupdf / fitz) ---
        try:
            try:
                import pymupdf as fitz
            except ImportError:
                import fitz

            doc = fitz.open(stream=raw_bytes, filetype="pdf")
            for page_idx, page in enumerate(doc):
                t = page.get_text("text") or ""
                t_clean = cls.clean_extracted_text(t)
                if t_clean:
                    pages_text.append(t_clean)
            
            page_count = len(doc)
            doc.close()

            if pages_text:
                full_text = "\n\n".join(pages_text)
                return ExtractedDocument(
                    filename=filename,
                    text=full_text,
                    pages=pages_text,
                    page_count=page_count,
                    engine_used="PyMuPDF",
                    char_count=len(full_text),
                    is_scanned_suspicion=False
                )
            elif page_count > 0:
                # El PDF se abrió pero no devolvió texto; podría ser escaneado o requerir otro motor
                logger.warning(f"PyMuPDF no extrajo texto de {filename} ({page_count} págs). Probando siguiente motor...")
        except Exception as e:
            errors.append(f"PyMuPDF: {e}")
            logger.debug(f"PyMuPDF no pudo procesar {filename}: {e}")

        # --- MOTOR 2: pdfplumber ---
        try:
            import pdfplumber
            with pdfplumber.open(io.BytesIO(raw_bytes)) as pdf:
                page_count = len(pdf.pages)
                plumber_pages = []
                for p in pdf.pages:
                    t = p.extract_text() or ""
                    t_clean = cls.clean_extracted_text(t)
                    if t_clean:
                        plumber_pages.append(t_clean)

                if plumber_pages:
                    full_text = "\n\n".join(plumber_pages)
                    return ExtractedDocument(
                        filename=filename,
                        text=full_text,
                        pages=plumber_pages,
                        page_count=page_count,
                        engine_used="pdfplumber",
                        char_count=len(full_text),
                        is_scanned_suspicion=False
                    )
        except Exception as e:
            errors.append(f"pdfplumber: {e}")
            logger.debug(f"pdfplumber no pudo procesar {filename}: {e}")

        # --- MOTOR 3: pypdf ---
        try:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(raw_bytes), strict=False)
            page_count = len(reader.pages)
            pypdf_pages = []
            for p in reader.pages:
                t = p.extract_text() or ""
                t_clean = cls.clean_extracted_text(t)
                if t_clean:
                    pypdf_pages.append(t_clean)

            if pypdf_pages:
                full_text = "\n\n".join(pypdf_pages)
                return ExtractedDocument(
                    filename=filename,
                    text=full_text,
                    pages=pypdf_pages,
                    page_count=page_count,
                    engine_used="pypdf",
                    char_count=len(full_text),
                    is_scanned_suspicion=False
                )
        except Exception as e:
            errors.append(f"pypdf: {e}")
            logger.debug(f"pypdf no pudo procesar {filename}: {e}")

        # --- MOTOR 4: pypdfium2 ---
        try:
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(raw_bytes)
            page_count = len(pdf)
            pdfium_pages = []
            for i in range(page_count):
                page = pdf[i]
                textpage = page.get_textpage()
                t = textpage.get_text_range() or ""
                t_clean = cls.clean_extracted_text(t)
                if t_clean:
                    pdfium_pages.append(t_clean)

            if pdfium_pages:
                full_text = "\n\n".join(pdfium_pages)
                return ExtractedDocument(
                    filename=filename,
                    text=full_text,
                    pages=pdfium_pages,
                    page_count=page_count,
                    engine_used="pypdfium2",
                    char_count=len(full_text),
                    is_scanned_suspicion=False
                )
        except Exception as e:
            errors.append(f"pypdfium2: {e}")
            logger.debug(f"pypdfium2 no pudo procesar {filename}: {e}")

        # --- MOTOR 5: pdfminer.six ---
        try:
            from pdfminer.high_level import extract_text as pdfminer_extract
            t = pdfminer_extract(io.BytesIO(raw_bytes)) or ""
            t_clean = cls.clean_extracted_text(t)
            if t_clean:
                return ExtractedDocument(
                    filename=filename,
                    text=t_clean,
                    pages=[t_clean],
                    page_count=1,
                    engine_used="pdfminer.six",
                    char_count=len(t_clean),
                    is_scanned_suspicion=False
                )
        except Exception as e:
            errors.append(f"pdfminer: {e}")
            logger.debug(f"pdfminer no pudo procesar {filename}: {e}")

        # Si llegamos aquí, ningún motor pudo extraer texto
        # Verificar si es porque el PDF es una imagen escaneada o porque está protegido / dañado
        err_summary = "; ".join(errors) if errors else "Sin capa de texto digital detectada"
        raise ValueError(
            f"El documento PDF '{filename}' no contiene texto digital legible "
            f"(posible documento escaneado/imagen sin capa OCR o protegido). Detalle: {err_summary}"
        )

    @classmethod
    def _extract_docx(cls, filename: str, raw_bytes: bytes) -> ExtractedDocument:
        """Extrae texto estructurado de archivos Word .docx respetando párrafos y tablas."""
        try:
            import docx
            doc = docx.Document(io.BytesIO(raw_bytes))
            sections_text = []

            for p in doc.paragraphs:
                txt = p.text.strip()
                if txt:
                    sections_text.append(txt)

            for table in doc.tables:
                table_rows = []
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if cells:
                        table_rows.append(" | ".join(cells))
                if table_rows:
                    sections_text.append("\n".join(table_rows))

            full_text = cls.clean_extracted_text("\n\n".join(sections_text))
            if not full_text:
                raise ValueError(f"El archivo DOCX '{filename}' no contiene texto legible.")

            return ExtractedDocument(
                filename=filename,
                text=full_text,
                pages=[full_text],
                page_count=1,
                engine_used="python-docx",
                char_count=len(full_text)
            )
        except Exception as e:
            logger.error(f"Error procesando DOCX {filename}: {e}")
            raise ValueError(f"No se pudo extraer texto del archivo DOCX '{filename}': {str(e)}")

    @classmethod
    def _extract_text_file(cls, filename: str, raw_bytes: bytes) -> ExtractedDocument:
        """
        Decodifica archivos de texto plano, Markdown, CSV, JSON con detección multi-encoding:
        UTF-8 -> UTF-8-SIG -> Latin-1 (ISO-8859-1) -> CP1252 -> UTF-16
        """
        # El BOM decide primero: latin-1 nunca falla al decodificar y dejaría UTF-16 en mojibake
        if raw_bytes[:2] in (b"\xff\xfe", b"\xfe\xff"):
            encodings = ["utf-16", "utf-8", "latin-1"]
        else:
            encodings = ["utf-8", "utf-8-sig", "cp1252", "latin-1", "iso-8859-15"]
        text = None
        used_enc = "utf-8"

        for enc in encodings:
            try:
                text = raw_bytes.decode(enc)
                used_enc = enc
                break
            except (UnicodeDecodeError, LookupError):
                continue

        if text is None:
            text = raw_bytes.decode("utf-8", errors="replace")
            used_enc = "utf-8 (replace)"

        cleaned = cls.clean_extracted_text(text)
        if not cleaned:
            raise ValueError(f"El archivo de texto '{filename}' no contiene texto legible.")

        return ExtractedDocument(
            filename=filename,
            text=cleaned,
            pages=[cleaned],
            page_count=1,
            engine_used=f"text_decode ({used_enc})",
            char_count=len(cleaned)
        )


# Instancia singleton predeterminada
document_extractor = DocumentExtractor()
