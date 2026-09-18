from app.core.chunking import StructuredChunker
from app.core.document_extractor import DocumentExtractor

chunker = StructuredChunker()


def test_conserva_jerarquia_de_encabezados():
    doc = "# Manual\n\n## Seguridad\n\nLas contraseñas caducan cada 90 días en toda la organización corporativa.\n"
    chunks = chunker.chunk_document(doc, "manual.md")
    assert chunks
    assert chunks[0].header_path == "Manual > Seguridad"
    assert chunks[0].text.startswith("[Manual > Seguridad]")


def test_tabla_se_mantiene_como_unidad_atomica():
    filas_txt = [f"| Servicio de consultoría número {i} | {i}00 € al mes | SLA de 24 horas |" for i in range(1, 6)]
    tabla = "\n".join(["| Producto | Precio | SLA |", "|---|---|---|"] + filas_txt)
    chunks = chunker.chunk_document(f"# Catálogo\n\n{tabla}\n", "catalogo.md")

    completos = [c for c in chunks if c.metadata.get("is_table") and c.text.count("Servicio") == 5]
    assert len(completos) == 1, "la tabla completa debe conservarse en un solo fragmento"

    filas = [c for c in chunks if "Fila" in c.header_path]
    assert len(filas) == 5, "cada fila genera además su propio fragmento con cabecera"
    assert all("| Producto | Precio | SLA |" in c.text for c in filas)


def test_filas_de_tabla_demasiado_cortas_no_generan_fragmento():
    tabla = "\n".join(["| A | B |", "|---|---|"] + [f"| x{i} | {i} |" for i in range(1, 6)])
    chunks = chunker.chunk_document(f"# T\n\n{tabla}\n", "mini.md")
    assert [c for c in chunks if "Fila" in c.header_path] == []


def test_texto_largo_se_divide_respetando_el_tamano():
    parrafo = "Esta es una frase de prueba con longitud suficiente para medir el troceado. " * 8
    doc = "\n\n".join([parrafo] * 6)
    chunks = chunker.chunk_document(doc, "largo.txt")
    assert len(chunks) > 1
    assert max(len(c.text) for c in chunks) <= chunker.target_chunk_size * 1.6


def test_ids_de_fragmento_llevan_el_nombre_del_documento():
    chunks = chunker.chunk_document("Contenido corporativo suficientemente largo para superar el mínimo exigido.", "informe anual.pdf")
    assert chunks[0].chunk_id.startswith("informe anual.pdf_chunk_")


def test_fragmentos_muy_cortos_se_descartan():
    assert chunker.chunk_document("ok\n", "corto.txt") == []


def test_extractor_texto_detecta_utf16_y_limpia_guiones():
    doc = DocumentExtractor.extract_from_bytes("u16.txt", "Informa-\nción confidencial de la compañía".encode("utf-16"))
    assert "Información confidencial" in doc.text
    assert doc.engine_used.startswith("text_decode (utf-16")


def test_extractor_rechaza_archivo_vacio():
    try:
        DocumentExtractor.extract_from_bytes("vacio.txt", b"")
        assert False, "debe lanzar ValueError"
    except ValueError:
        pass


def test_una_seccion_corta_pero_unica_no_se_pierde():
    """
    Regresión: el mínimo por caracteres borraba secciones de una frase. El dato quedaba
    sin respuesta posible aunque el documento estuviera subido y contase como indexado.
    """
    doc = "# Preaviso\n\nEl plazo de preaviso es de 15 dias naturales.\n"
    chunks = chunker.chunk_document(doc, "convenio.md")
    assert any("15 dias naturales" in c.text for c in chunks)


def test_un_documento_corto_entero_se_indexa():
    doc = "La tarifa de mantenimiento asciende a 12400 euros anuales."
    assert chunker.chunk_document(doc, "tarifa.txt"), "58 caracteres, pero es todo el documento"
