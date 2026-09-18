"""
Mide la afirmacion central del proyecto: que responde cuando la documentacion sostiene
la respuesta y se calla cuando no.

Las dos mitades del conjunto miden cosas distintas y ninguna basta sola. Un sistema que
siempre responde acierta el 100% de las respondibles e inventa en las 8 restantes; uno
que siempre se calla no inventa nunca y no sirve para nada. El resultado util es el par.

Uso (con el servidor levantado y el corpus de sample_docs/ indexado):

    python eval/evaluar.py
    python eval/evaluar.py --url http://127.0.0.1:8000 --salida eval/resultado.json
"""
import argparse
import json
import pathlib
import sys
import time

import httpx

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from app.core.evidence import LEVEL_D_NO_EVIDENCE, is_abstention  # noqa: E402

AQUI = pathlib.Path(__file__).resolve().parent


def se_abstuvo(respuesta: dict) -> bool:
    """Mismo criterio que usa la interfaz, no uno inventado para el informe."""
    return (respuesta.get("uncertainty_level") == LEVEL_D_NO_EVIDENCE
            or is_abstention(respuesta.get("text", "")))


def preguntar(cliente: httpx.Client, url: str, pregunta: str) -> dict:
    t0 = time.monotonic()
    r = cliente.post(f"{url}/ask", json={"question": pregunta}, timeout=180.0)
    r.raise_for_status()
    datos = r.json()
    datos["_segundos"] = round(time.monotonic() - t0, 1)
    return datos


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://127.0.0.1:8000")
    p.add_argument("--salida", default=str(AQUI / "resultado.json"))
    args = p.parse_args()

    conjunto = json.loads((AQUI / "preguntas.json").read_text(encoding="utf-8"))
    filas = []

    with httpx.Client() as cliente:
        try:
            cliente.get(f"{args.url}/health", timeout=10.0).raise_for_status()
        except Exception as e:
            print(f"No hay servidor en {args.url}: {e}")
            return 2

        for caso in conjunto["respondibles"]:
            r = preguntar(cliente, args.url, caso["pregunta"])
            abstuvo = se_abstuvo(r)
            texto = r.get("text", "")
            acerto = any(m.lower() in texto.lower() for m in caso["debe_contener"])
            filas.append({
                "tipo": "respondible", "pregunta": caso["pregunta"],
                "abstuvo": abstuvo, "acerto": bool(acerto and not abstuvo),
                "nivel": r.get("uncertainty_level"), "ruta": r.get("route"),
                "segundos": r["_segundos"], "respuesta": texto[:300],
            })
            print(f"  [{'CALLA' if abstuvo else ('OK   ' if acerto else 'FALLA')}] {caso['pregunta'][:70]}")

        for caso in conjunto["fuera_de_corpus"]:
            r = preguntar(cliente, args.url, caso["pregunta"])
            abstuvo = se_abstuvo(r)
            filas.append({
                "tipo": "fuera_de_corpus", "pregunta": caso["pregunta"],
                "abstuvo": abstuvo, "acerto": abstuvo,
                "nivel": r.get("uncertainty_level"), "ruta": r.get("route"),
                "segundos": r["_segundos"], "respuesta": r.get("text", "")[:300],
            })
            print(f"  [{'OK   ' if abstuvo else 'INVEN'}] {caso['pregunta'][:70]}")

    resp = [f for f in filas if f["tipo"] == "respondible"]
    fuera = [f for f in filas if f["tipo"] == "fuera_de_corpus"]
    contestadas = [f for f in resp if not f["abstuvo"]]

    resumen = {
        "respondibles": len(resp),
        "respondidas": len(contestadas),
        "respondidas_con_el_dato_correcto": sum(1 for f in resp if f["acerto"]),
        "fuera_de_corpus": len(fuera),
        "abstenciones_correctas": sum(1 for f in fuera if f["abstuvo"]),
        "invenciones": sum(1 for f in fuera if not f["abstuvo"]),
        "segundos_mediana": sorted(f["segundos"] for f in filas)[len(filas) // 2],
    }

    print("\n" + "=" * 62)
    print(f"  Preguntas que el corpus responde     {resumen['respondibles']}")
    print(f"    contestadas (no se callo)          {resumen['respondidas']}")
    print(f"    con el dato correcto               {resumen['respondidas_con_el_dato_correcto']}")
    print(f"  Preguntas que el corpus NO responde  {resumen['fuera_de_corpus']}")
    print(f"    se abstuvo                         {resumen['abstenciones_correctas']}")
    print(f"    se invento algo                    {resumen['invenciones']}")
    print(f"  Latencia mediana                     {resumen['segundos_mediana']} s")
    print("=" * 62)

    pathlib.Path(args.salida).write_text(
        json.dumps({"resumen": resumen, "detalle": filas}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nDetalle por pregunta en {args.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
