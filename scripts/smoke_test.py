"""Test dymny całego potoku na danych z paczki: preprocessing, moduł tekstu, OSINT, audio i wideo, API.

    docker compose exec app python scripts/smoke_test.py            # pełny (modele: embeddingi, Whisper, ECAPA)
    docker compose exec app python scripts/smoke_test.py --fast     # bez modeli, ok. 2 min
    docker compose exec app python scripts/smoke_test.py --enrich   # OSINT z DNS/WHOIS/TLS (wolniej, internet)

Każdy test kończy się: OK, BŁĄD albo POMINIĘTO (z powodem, np. brak biblioteki).
Wyniki pośrednie trafiają do folderu tymczasowego – data/store zostaje nietknięte.
Kod wyjścia 1, gdy którykolwiek test się nie powiódł.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--fast", action="store_true", help="bez modeli (embeddingi tekstu, Whisper, ECAPA)")
ap.add_argument("--enrich", action="store_true", help="OSINT z zapytaniami DNS/WHOIS/TLS")
ap.add_argument("--data", default=os.environ.get("DATA_DIR", "data"), help="folder z BACKUP i DANE")
ARGS = ap.parse_args()

TMP = Path(tempfile.mkdtemp(prefix="smoke_"))
os.environ["STORE_DIR"] = str(TMP / "store")
os.environ.setdefault("ANALYZE_DUMP_DIR", str(TMP / "dump"))   # API zapisuje tu podgląd odpowiedzi
if ARGS.fast:
    os.environ["TEXT_EMBEDDINGS"] = "0"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules"))

DATA = Path(ARGS.data)
BACKUP, DANE = DATA / "BACKUP", DATA / "DANE"
RESULTS: list[tuple[str, str, str, str]] = []   # (moduł, nazwa, status, szczegóły)
CACHE: dict[str, object] = {}


def check(module: str, name: str):
    """Dekorator: uruchamia test, łapie wyjątki, zapisuje wynik."""
    def wrap(fn):
        t0 = time.time()
        try:
            detail = fn() or ""
            status = "OK"
        except Skip as s:
            status, detail = "POMINIĘTO", str(s)
        except AssertionError as e:
            status, detail = "BŁĄD", str(e) or "warunek niespełniony"
        except Exception as e:  # noqa: BLE001
            status, detail = "BŁĄD", f"{e.__class__.__name__}: {e}"
            traceback.print_exc(limit=3)
        dt = time.time() - t0
        mark = {"OK": "✓", "BŁĄD": "✗", "POMINIĘTO": "–"}[status]
        print(f"  {mark} [{module}] {name} ({dt:.0f} s){': ' + detail if detail else ''}", flush=True)
        RESULTS.append((module, name, status, detail))
        return fn
    return wrap


class Skip(Exception):
    pass


def need(path: Path) -> Path:
    if not path.exists():
        raise Skip(f"brak danych: {path}")
    return path


def ingest_case(name: str, **kw):
    from fakecollector.ingest import ingest
    key = f"inc:{name}:{sorted(kw.items())}"
    if key not in CACHE:
        opts = dict(fetch=False, dedup=False, save=False, store_root=TMP / "store")
        opts.update(kw)
        CACHE[key] = ingest(str(need(DATA / name)), **opts)
    return CACHE[key]


def text_feats(name: str):
    from fakecollector.features import text
    key = f"text:{name}"
    if key not in CACHE:
        CACHE[key] = text.extract(ingest_case(name, ocr=False))
    return CACHE[key]


print(f"Dane: {DATA.resolve()}   tryb: {'szybki (bez modeli)' if ARGS.fast else 'pełny'}   tmp: {TMP}\n")

# ============================================================================ preprocessing
print("Preprocessing")


@check("preprocessing", "BACKUP/p1: tekst strony, pominięta strona błędu 502, składniki dla modułów")
def _():
    inc = ingest_case("BACKUP/p1")
    r = inc.routes()
    words = len(inc.text.split())
    assert words > 1000, f"za mało tekstu: {words} słów"
    assert "Bad gateway" not in inc.text[:300], "wybrano stronę błędu 502"
    assert inc.text_origin == ["html"], inc.text_origin
    assert r["text"] and r["osint"] and r["image"], f"puste składniki: {r}"
    assert "dorquesio.click" in inc.urls[0], inc.urls[:2]
    return f"{words} słów, składniki {r}"


@check("preprocessing", "DANE/61: trzy strony w jednym przypadku")
def _():
    inc = ingest_case("DANE/61", ocr=False)
    words = len(inc.text.split())
    assert words >= 600, f"{words} słów – nie połączono stron"
    return f"{words} słów"


@check("preprocessing", "DANE/14: wideo ze strony sklejone ze strumienia, brak zgłoszony")
def _():
    inc = ingest_case("DANE/14", ocr=False, video=False, assets=False)
    videos = inc.of_kind("video")
    assert len(videos) == 2, f"filmów: {len(videos)} (oczekiwano 2: nagranie ekranu + strumień)"
    assert any("z 105" in m for m in inc.missing), inc.missing
    return f"{len(videos)} filmy, braki: {[m for m in inc.missing if 'strumienia' in m]}"


@check("preprocessing", "duplikat: ten sam folder drugi raz")
def _():
    from fakecollector.ingest import ingest
    opts = dict(fetch=False, ocr=False, save=False, store_root=TMP / "dedup")
    a = ingest(str(need(BACKUP / "p3")), **opts)
    b = ingest(str(BACKUP / "p3"), **opts)
    assert a.duplicate_of is None and b.duplicate_of == a.id, (a.duplicate_of, b.duplicate_of)
    return f"{b.id} = duplikat {a.id}"


@check("preprocessing", "upload z frontu: tekst + pliki")
def _():
    from fakecollector.ingest import ingest_upload
    files = [(p.name, p.read_bytes()) for p in need(BACKUP / "p3").iterdir() if p.is_file()]
    inc = ingest_upload(text="Zobacz to: falmurek.live", files=files, fetch=False, ocr=False,
                        dedup=False, save=False, store_root=TMP / "store")
    assert inc.input_type == "upload"
    assert len(inc.text.split()) > 2000, len(inc.text.split())
    assert any("falmurek" in u for u in inc.urls), inc.urls
    return f"{len(files)} plików, {len(inc.text.split())} słów"


@check("preprocessing", "sam tekst z linkiem")
def _():
    from fakecollector.ingest import ingest
    inc = ingest("Super okazja! Wejdź na https://grentavo.click/abc i zarejestruj się", fetch=False,
                 save=False, dedup=False, store_root=TMP / "store")
    assert inc.text_origin == ["input"] and inc.urls == ["https://grentavo.click/abc"], (inc.text_origin, inc.urls)


# ============================================================================ moduł tekstu
print("\nModuł tekstu" + (" (bez embeddingów)" if ARGS.fast else ""))


@check("tekst", "BACKUP/p1: osoby, platforma, kwoty, odciski")
def _():
    f = text_feats("BACKUP/p1")
    assert "Rafał Brzoska" in f["persons"], f["persons"]
    assert any("świtowin" in k.lower() for k in f["keywords"]), f["keywords"][:8]
    assert f["money"], "brak kwot"
    assert len(f["minhash"]) == 128 and f["text_sha256"], "brak odcisków"
    if not ARGS.fast:
        assert f.get("text_emb") and len(f["text_emb"]) >= 384, "brak embeddingu tekstu"
    return f"osoby {list(f['persons'])[:2]}, kwot {len(f['money'])}"


def text_pair(a: str, b: str):
    from fakecollector.features import text
    return text.compare(text_feats(a), text_feats(b))


@check("tekst", "ta sama kampania Brzoski (p1 ~ p15, różne domeny) – wysoki wynik")
def _():
    r = text_pair("BACKUP/p1", "BACKUP/p15")
    assert r["text_score"] >= 0.8, r["text_score"]
    return f"wynik {r['text_score']}"


@check("tekst", "ten sam szablon z podmienioną osobą (p3 Stanowski ~ p4 Sekielski)")
def _():
    r = text_pair("BACKUP/p3", "BACKUP/p4")
    assert r["text_score"] >= 0.7, r["text_score"]
    return f"wynik {r['text_score']}"


@check("tekst", "różne tematy (p1 Brzoska ~ p9 przepis) – niski wynik")
def _():
    r = text_pair("BACKUP/p1", "BACKUP/p9")
    assert r["text_score"] <= 0.4, r["text_score"]
    return f"wynik {r['text_score']}"


# ============================================================================ OSINT
print("\nModuł OSINT" + (" (z DNS/WHOIS/TLS)" if ARGS.enrich else " (bez zapytań do internetu)"))


def osint_rec(name: str):
    key = f"osint:{name}"
    if key not in CACHE:
        try:
            import osint  # noqa: F401
        except ImportError as e:
            raise Skip(f"moduł OSINT niedostępny ({e})")
        from fakecollector.ingest import run_osint
        inc = ingest_case(name, ocr=False)
        rec = run_osint(inc, enrich=ARGS.enrich)
        assert rec, f"run_osint nic nie zwrócił: {inc.errors[-1:]}"
        CACHE[key] = rec
    return CACHE[key]


@check("osint", "BACKUP/p1: domena i pivoty")
def _():
    rec = osint_rec("BACKUP/p1")
    doms = rec["landing"]["registered_domains"]
    assert "dorquesio.click" in doms, doms
    assert rec["quality"]["total_pivots"] >= 20, rec["quality"]
    return f"domeny {doms}, pivotów {rec['quality']['total_pivots']}"


@check("osint", "ta sama kampania (p1 ~ p2, ta sama domena) – wysoki wynik")
def _():
    a, b = osint_rec("BACKUP/p1"), osint_rec("BACKUP/p2")
    from osint.correlate import compare
    r = compare(a, b)
    assert r["score"] >= 0.75, r["score"]
    return f"wynik {r['score']} ({r['label']})"


@check("osint", "różne kampanie (p1 ~ p9) – niski wynik")
def _():
    a, b = osint_rec("BACKUP/p1"), osint_rec("BACKUP/p9")
    from osint.correlate import compare
    r = compare(a, b)
    assert r["score"] < 0.5, r["score"]
    return f"wynik {r['score']} ({r['label']})"


# ============================================================================ audio i wideo
print("\nModuł audio i wideo" + (" (bez Whispera i ECAPA)" if ARGS.fast else ""))
AV_OPTS = dict(asr=not ARGS.fast, voice=not ARGS.fast)


def av_feats(name: str):
    from fakecollector.features import av
    key = f"av:{name}"
    if key not in CACHE:
        CACHE[key] = av.analyze_path(need(DATA / name) if not Path(name).is_absolute() else Path(name), **AV_OPTS)
    return CACHE[key]


@check("audio/wideo", "DANE/5: metadane, odciski dźwięku i obrazu, napisy" + ("" if ARGS.fast else ", transkrypcja, głos"))
def _():
    f = av_feats("DANE/5")
    it = f["items"][0]
    assert 190 < it["duration"] < 205, it["duration"]
    assert len(it["audio_fp_hashes"]) > 1000 and len(it["video_fp"]) > 100, "za małe odciski"
    assert it["onscreen_text"], "brak napisów z ekranu"
    for e in it["errors"]:
        raise AssertionError(e)
    if not ARGS.fast:
        assert len(it["transcript"].split()) > 50, f"transkrypcja: {it['transcript'][:80]!r}"
        assert it["voice_emb"] and len(it["voice_emb"]) == 192, "brak odcisku głosu"
        return f"transkrypcja: {it['transcript'][:70]}…"
    return f"{it['duration']} s, {len(it['audio_fp_hashes'])} par dźwięku, {len(it['video_fp'])} klatek"


@check("audio/wideo", "przerobiona kopia DANE/5 (wycięta, pomniejszona, ciszej) – rozpoznana")
def _():
    from fakecollector.features import av
    src = next(need(DANE / "5").glob("*.mp4"))
    copy = TMP / "kopia" / "kopia.mp4"
    copy.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "40", "-t", "50", "-i", str(src), "-vf", "scale=360:360",
                    "-af", "volume=0.5", str(copy)], check=True, timeout=300)
    r = av.compare(av_feats("DANE/5"), av.analyze_path(copy.parent, **AV_OPTS))
    s = r["signals"]
    assert r["av_score"] >= 0.9 and s["same_audio"] >= 0.9 and s["same_video"] >= 0.9, s
    if not ARGS.fast:
        assert s["voice_cosine"] >= 0.8, s["voice_cosine"]
    return f"wynik {r['av_score']}, dźwięk {s['same_audio']}, obraz {s['same_video']}, głos {s['voice_cosine']}"


@check("audio/wideo", "różne reklamy (DANE/5 ~ DANE/14) – niski wynik")
def _():
    from fakecollector.features import av
    r = av.compare(av_feats("DANE/5"), av_feats("DANE/14"))
    assert (r["av_score"] or 0) <= 0.3, r
    return f"wynik {r['av_score']}, głos {r['signals']['voice_cosine']}"


@check("audio/wideo", "transkrypcja/napisy trafiają do modułu tekstu (DANE/5)")
def _():
    from fakecollector.features import av
    inc = ingest_case("DANE/5", ocr=False, video=False)
    assert inc.text_origin == ["adlib"], f"przed: {inc.text_origin}"
    av.attach_to_incident(inc, av_feats("DANE/5"))
    want = "ocr" if ARGS.fast else "transcript"
    assert inc.text_origin == [want], f"po: {inc.text_origin}, oczekiwano {want}"
    assert len(inc.text.split()) >= 15
    return f"tekst z: {inc.text_origin[0]}, {len(inc.text.split())} słów"


# ============================================================================ obraz i rekord materiału
print("\nModuł obrazu i rekord materiału")


def record_of(name: str):
    key = f"rec:{name}"
    if key not in CACHE:
        from fakecollector.record import process_incident
        inc = ingest_case(name, ocr=False)
        CACHE[key] = process_incident(inc, save=False, asr=False, osint=True, enrich=False, image=True)
    return CACHE[key]


@check("obraz", "BACKUP/p1: obrazy materiału przez moduł obrazu (hashe, CLIP, twarze)")
def _():
    try:
        import fakecollector.features.image  # noqa: F401
    except ImportError as e:
        raise Skip(f"moduł obrazu niedostępny ({e})")
    out = record_of("BACKUP/p1")
    mod = out["record"]["modules"]["image"]
    assert mod["status"] == "ok", mod
    im = out["_results"].image
    assert im["images_analyzed"] >= 1, im
    with_vec = sum(1 for it in im["items"] if it["cechy"].get("wektor"))
    if not ARGS.fast:
        assert with_vec >= 1, "brak wektorów CLIP – pobierz modele: python scripts/pobierz_modele_obrazu.py " \
                              "--modele siglip2,clip,buffalo_l,yunet_sface,magface,facetorch"
    return f"{im['images_analyzed']} obrazów, wektorów {with_vec}, osoby na obrazach {im['persons_visual']}"


@check("rekord", "BACKUP/p1: rekord ma podsumowanie, klucze i wektory")
def _():
    rec = record_of("BACKUP/p1")["_record"]
    kinds = {k for k, _, _ in rec.keys}
    assert {"text_lsh", "skel_lsh", "domain", "text_sha256"} <= kinds, kinds
    assert rec.summary["domain"] == "dorquesio.click", rec.summary["domain"]
    if rec.summary["modules"]["osint"]["status"] == "ok":
        assert any(k.startswith("osint:") for k in kinds), "brak kluczy OSINT"
    if not ARGS.fast:
        assert any(v[0] == "text" for v in rec.vectors), "brak wektora tekstu"
    return f"{len(rec.keys)} kluczy, {len(rec.vectors)} wektorów, moduły " + \
        ", ".join(f"{m}:{v['status']}" for m, v in rec.summary["modules"].items())


@check("rekord", "zapis w bazie i kandydaci: p1 znajduje p15 (ta sama kampania), nie p9")
def _():
    if not os.environ.get("DATABASE_URL"):
        raise Skip("brak DATABASE_URL (uruchom w kontenerze app)")
    from fakecollector.record import delete_material, find_candidates, load_record, save_record
    ids = []
    try:
        for name in ("BACKUP/p1", "BACKUP/p15", "BACKUP/p9"):
            rec = record_of(name)["_record"]
            save_record(rec)
            ids.append(rec.id)
        back = load_record(ids[0])
        assert back and set(back.features) == set(record_of("BACKUP/p1")["_record"].features), "odczyt rekordu"
        cands = {c["id"]: c for c in find_candidates(ids[0])}
        assert ids[1] in cands, f"p15 nie jest kandydatem: {list(cands)}"
        sig15 = [s["signal"] for s in cands[ids[1]]["signals"]]
        assert "text_lsh" in sig15 or "text_sha256" in sig15, sig15
        rank9 = cands.get(ids[2], {}).get("rank", 0)
        assert rank9 < cands[ids[1]]["rank"], f"p9 ({rank9}) wyżej niż p15"
        return f"p15: ranga {cands[ids[1]]['rank']} ({', '.join(sig15[:4])}); p9: {rank9}"
    finally:
        for i in ids:
            try:
                delete_material(i)
            except Exception:  # noqa: BLE001
                pass


@check("grupowanie", "kampanie: Brzoska (p1+p15+DANE/1) i p3+p4 osobno, ale ten sam operator; p9 osobno")
def _():
    if not os.environ.get("DATABASE_URL"):
        raise Skip("brak DATABASE_URL (uruchom w kontenerze app)")
    from fakecollector.grouping import compare_materials, rebuild
    from fakecollector.record import delete_material, save_record
    names = ["BACKUP/p1", "BACKUP/p15", "DANE/1", "BACKUP/p3", "BACKUP/p4", "BACKUP/p9"]
    ids = {}
    try:
        for name in names:
            rec = record_of(name)["_record"]
            save_record(rec)
            ids[name] = rec.id
        res = rebuild(ids=list(ids.values()), write=False)
        n = {v: k for k, v in ids.items()}
        show = [[n[m] for m in g] for g in res["clusters"]]
        group = {m: tuple(sorted(g)) for g in res["clusters"] for m in g}
        p1, p3 = ids["BACKUP/p1"], ids["BACKUP/p3"]
        assert group.get(p1) and group[p1] == group.get(ids["BACKUP/p15"]) == group.get(ids["DANE/1"]), \
            f"Brzoska rozbity: {show}"
        assert group.get(p3) and group[p3] == group.get(ids["BACKUP/p4"]), f"p3/p4 rozbite: {show}"
        assert group[p1] != group[p3], f"Brzoska i Stanowski w jednej kampanii: {show}"
        assert ids["BACKUP/p9"] not in group, "p9 dołączony do kampanii"
        same_op = any(p1 in g and p3 in g for g in res["operator_clusters"])
        r = compare_materials(p1, p3)
        assert same_op, f"brak wspólnego operatora p1~p3 (operator {r.operator_score}, {r.operator_reasons[:2]})"
        return f"kampanie: {show}; p1~p3: kampania {r.score}, operator {r.operator_score} ({r.operator_reasons[:1]})"
    finally:
        for i in ids.values():
            try:
                delete_material(i)
            except Exception:  # noqa: BLE001
                pass


@check("graf", "graf z bazy (kampanie, materiały, ślady) i kopia w Neo4j")
def _():
    if not os.environ.get("DATABASE_URL"):
        raise Skip("brak DATABASE_URL (uruchom w kontenerze app)")
    from fakecollector.graph import build_graph
    g = build_graph()
    if not g["nodes"]:
        raise Skip("pusta baza – najpierw: python -m fakecollector.record add data/BACKUP data/DANE")
    types = g["stats"]["by_node_type"]
    assert "material" in types, types
    ids = {n["id"] for n in g["nodes"]}
    assert all(e["source"] in ids and e["target"] in ids for e in g["edges"]), "krawędź do nieistniejącego węzła"
    detail = f"węzły {types}, krawędzie {g['stats']['by_edge_type']}"
    if os.environ.get("NEO4J_URI"):
        from fakecollector.graph.neo4j_sync import sync
        r = sync(g)
        assert r["nodes"] == len(g["nodes"]), r
        detail += f"; Neo4j: {r['nodes']} węzłów, {r['edges']} krawędzi"
    return detail



@check("ewaluacja", "metryki (ARI, pary) i ocena przypisywania na przykładzie")
def _():
    from fakecollector.eval import metrics
    from fakecollector.eval.run import MODULE_NAMES, Pair, _getter, evaluate
    gold = {"a": 1, "b": 1, "c": 2, "d": 2, "e": 3}
    assert metrics.adjusted_rand(gold, gold) == 1.0
    assert metrics.adjusted_rand({k: k for k in gold}, gold) == 0.0
    pairs = {("a", "b"): Pair("a", "b", contrib={"text": 0.9}),
             ("c", "d"): Pair("c", "d", contrib={"osint": 0.8}),
             ("b", "e"): Pair("b", "e", contrib={"image": 0.3})}
    r = evaluate(list(gold), gold, _getter(pairs, frozenset(MODULE_NAMES)), 0.75)
    assert r["pairs"]["f1"] == 1.0 and r["assign_known"]["accuracy"] == 1.0 and r["assign_new"]["accuracy"] == 1.0, r
    no_text = evaluate(list(gold), gold, _getter(pairs, frozenset(MODULE_NAMES) - {"text"}), 0.75)
    assert no_text["pairs"]["recall"] == 0.5, no_text["pairs"]
    return "ARI, F1, „wyjmij jeden” i ablacja liczą się poprawnie"

# ============================================================================ API
print("\nAPI")


@check("api", "POST /analyze z plikiem strony (p3), bez zapisu do bazy")
def _():
    try:
        from fastapi.testclient import TestClient
        from fakecollector.api.main import app
    except ImportError as e:
        raise Skip(f"brak fastapi/httpx ({e})")
    client = TestClient(app)
    html = next(p for p in need(BACKUP / "p3").iterdir() if p.suffix == ".html")
    resp = client.post("/analyze", files=[("files", (html.name, html.read_bytes(), "text/html"))],
                       data={"fetch": "false", "osint": "true" if ARGS.enrich else "false", "av": "false",
                             "image": "false", "save": "false"})
    assert resp.status_code == 200, f"{resp.status_code}: {resp.text[:300]}"
    d = resp.json()
    assert d["routes"]["text"] >= 1 and d["text_features"]["persons"], d.get("routes")
    assert not d["text_features"].get("minhash") or isinstance(d["text_features"]["minhash"], str), \
        "wektory nie powinny iść na front"
    return f"osoby {list(d['text_features']['persons'])[:2]}"


# ============================================================================ podsumowanie
ok = sum(r[2] == "OK" for r in RESULTS)
bad = [r for r in RESULTS if r[2] == "BŁĄD"]
skip = [r for r in RESULTS if r[2] == "POMINIĘTO"]
print(f"\nWynik: {ok} OK, {len(bad)} błędów, {len(skip)} pominiętych (z {len(RESULTS)})")
for m, n, _, d in bad:
    print(f"  ✗ [{m}] {n}: {d}")
for m, n, _, d in skip:
    print(f"  – [{m}] {n}: {d}")
sys.exit(1 if bad else 0)
