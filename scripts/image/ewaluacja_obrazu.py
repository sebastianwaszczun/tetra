"""Raport obrazu z pomiarami hashy, wyglądu i twarzy."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fakecollector.features.image.ewaluacja.ewaluacja_hashe import ewaluuj_hashe
from fakecollector.features.image.ewaluacja.ewaluacja_qr import ewaluuj_qr
from fakecollector.features.image.ewaluacja.ewaluacja_twarzy import ewaluuj_twarze
from fakecollector.features.image.ewaluacja.ewaluacja_wygladu import ewaluuj_wyglad
from fakecollector.features.image.wzorzec import wczytaj_kreacje, wczytaj_pary

NAZWY_POMIAROW = {
    "bazowy": "liczniki bazowe",
    "hashe": "hashe obrazów",
    "wyglad": "modele wizualne",
    "twarze": "twarze",
    "jakosc-twarzy": "jakość twarzy",
    "facetorch": "FaceTorch (orientacyjnie)",
    "qr": "QR",
    "porownania": "porównanie obrazów",
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dane", type=Path, default=Path("data"))
    parser.add_argument("--pomiar", choices=NAZWY_POMIAROW, default="bazowy")
    parser.add_argument("--zapisz-pary", action="store_true", help="Hashe: zapisz pary SHA-256 i kandydatów do przeglądu")
    parser.add_argument("--uwzglednij-szkice", action="store_true", help="Wygląd i twarze: uwzględnij ręcznie dopuszczone szkice")
    parser.add_argument("--nieznane", nargs="*", default=(), help="Twarze i ich jakość: identyfikatory kreacji z twarzami spoza bazy")
    parser.add_argument("--portrety", type=Path, help="FaceTorch: lokalny katalog portretów kontrolnych w data/nask/DANE")
    parser.add_argument("--wydajnosc", action="store_true", help="Porównania: zmierz analizę po rozgrzewce")
    args = parser.parse_args()
    dane = args.dane
    if args.wydajnosc:
        if args.pomiar != "porownania":
            parser.error("--wydajnosc wymaga --pomiar porownania")
        from fakecollector.features.image.ewaluacja.ewaluacja_porownania import zmierz_wydajnosc

        raport = zmierz_wydajnosc(dane)
        katalog = dane / "raporty"
        katalog.mkdir(parents=True, exist_ok=True)
        teraz = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        plik = katalog / f"wydajnosc-obrazu-{teraz}.json"
        plik.write_text(json.dumps(raport, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(plik)
        return
    hashe = ewaluuj_hashe(dane, args.zapisz_pary) if args.pomiar == "hashe" else None
    wyglad = ewaluuj_wyglad(dane, args.uwzglednij_szkice) if args.pomiar == "wyglad" else None
    twarze = ewaluuj_twarze(dane, args.uwzglednij_szkice, tuple(args.nieznane)) if args.pomiar == "twarze" else None
    qr = ewaluuj_qr(dane) if args.pomiar == "qr" else None
    jakosc_twarzy = None
    if args.pomiar == "jakosc-twarzy":
        from fakecollector.features.image.ewaluacja.ewaluacja_jakosci_twarzy import ewaluuj_jakosc_twarzy

        jakosc_twarzy = ewaluuj_jakosc_twarzy(dane, tuple(args.nieznane), args.uwzglednij_szkice)
    facetorch = None
    if args.pomiar == "facetorch":
        from fakecollector.features.image.ewaluacja.ewaluacja_facetorch import ewaluuj_facetorch

        facetorch = ewaluuj_facetorch(dane, args.portrety)
    porownania, pary_html = None, []
    if args.pomiar == "porownania":
        from fakecollector.features.image.ewaluacja.ewaluacja_porownania import ewaluuj_porownania

        porownania, pary_html = ewaluuj_porownania(dane)
    braki = []
    rozmiary = {}
    for nazwa, loader in (("kreacje", wczytaj_kreacje), ("pary_obrazow", wczytaj_pary)):
        if (dane / "wzorzec" / f"{nazwa}.json").is_file():
            wynik = loader(dane)
            rozmiary[nazwa] = len(wynik.kreacje if nazwa == "kreacje" else wynik)
        else:
            rozmiary[nazwa] = None
            braki.append(f"Brak wzorzec/{nazwa}.json")

    metryki = dict.fromkeys(("porownania", "dowody", "trafne_dopasowania", "falszywe_dopasowania"), 0)
    if hashe is not None:
        metryki.update(porownania=hashe["negatywy"] + hashe["liczba_wariantow"] + hashe["liczba_identycznych"],
                      dowody=hashe["liczba_identycznych"] + hashe["trafione_warianty"] + hashe["falszywe"],
                      trafne_dopasowania=hashe["liczba_identycznych"] + hashe["trafione_warianty"],
                      falszywe_dopasowania=hashe["falszywe"])
    if wyglad is not None:
        metryki = {
            f"{model}_{tryb}_top1": sum(w["top1"] for w in liczby["ranking_wariantow"].values())
            for model, tryby in wyglad["modele"].items() for tryb, liczby in tryby.items()
        }
    if twarze is not None:
        metryki = {f"{sigiel}_zgodne_liczby_twarzy": pomiar["zgodne_liczby"]
                   for sigiel, pomiar in twarze["modele"].items()}
    if jakosc_twarzy is not None:
        metryki = {"referencje": jakosc_twarzy["referencje"], "kontrole": len(jakosc_twarzy["kontrole"])}
    if facetorch is not None:
        metryki = {f"facetorch_{zbior}": pomiar["wyniki"] for zbior, pomiar in facetorch["zbiory"].items()}
    if qr is not None:
        metryki = {"qr_odczytane": sum(p["odczytano"] for p in qr["proby"]), "qr_falszywe": qr["falszywe"]}
    if porownania is not None:
        metryki = {"pary_diagnostyczne": porownania["diagnostyka"]["porownania"], "klatki": porownania["klatki_odczytane"]}
    katalog = dane / "raporty"
    katalog.mkdir(parents=True, exist_ok=True)
    poprzedni = max(katalog.glob("obraz-*.json"), default=None)
    stare = json.loads(poprzedni.read_text(encoding="utf-8"))["metryki"] if poprzedni else {}
    roznice = {k: v - stare[k] if k in stare else None for k, v in metryki.items()}
    teraz = datetime.now(timezone.utc)
    nazwa = f"obraz-{teraz:%Y%m%dT%H%M%S%fZ}-{uuid4().hex[:8]}"
    raport = {"pomiar": args.pomiar, "czas": teraz.isoformat(), "metryki": metryki, "dane": rozmiary,
              "braki": braki, "poprzedni": poprzedni.name if poprzedni else None, "zmiany": roznice}
    if hashe is not None:
        raport["hashe"] = hashe
        if hashe["kreacje_zatwierdzone"] == 0:
            braki.append("Brak zatwierdzonych wycinków i trudnych par kreacji w pomiarze.")
        if hashe["nakladki"] is None:
            braki.append("Brak zatwierdzonych prostokątów przycisku odtwarzania do pomiaru nakładek.")
        if not hashe["skalibrowano"]:
            braki.append("Nie wyznaczono progów na podstawie pomiaru. Zastosowano progi z konfiguracji.")
    if wyglad is not None:
        raport["wyglad"] = wyglad
        braki.extend(wyglad["braki"])
    if twarze is not None:
        raport["twarze"] = twarze
        braki.extend(twarze["braki"])
    if jakosc_twarzy is not None:
        raport["jakosc_twarzy"] = jakosc_twarzy
        braki.extend(jakosc_twarzy["braki"])
    if facetorch is not None:
        raport["facetorch"] = facetorch
        braki.extend(facetorch["braki"])
    if qr is not None:
        raport["qr"] = qr
    if porownania is not None:
        raport["porownania"] = porownania
    tabela = [f"{args.pomiar} — {NAZWY_POMIAROW[args.pomiar]}",
              "Metryka                     Wynik   Zmiana"]
    for k, v in metryki.items():
        delta = "—" if roznice[k] is None else f"{roznice[k]:+d}"
        tabela.append(f"{k:27} {v:5}   {delta}")
    tabela.extend(braki)
    if hashe is not None:
        tabela.append(f"T_p={hashe['T_p']}, T_d={hashe['T_d']}, zapas={hashe['zapas_bit']} bit, "
                      f"błędy z zapasem={hashe['falszywe_z_zapasem']}")
        tabela.append(f"Obrazy: {hashe['zasoby']}, pary kontrolne: {hashe['negatywy']}, sporne: {hashe['sporne']}")
        tabela.extend(f"Identyczne {k}: {v}" for k, v in hashe["identyczne"].items())
        if hashe["czas_ms"]:
            tabela.append(f"Czas na raster (ms): średnia {hashe['czas_ms']['srednia']}, mediana {hashe['czas_ms']['mediana']}")
        if hashe["nakladki"]:
            pomiar = hashe["nakladki"]
            tabela.append(f"Nakładki, {pomiar['pary']} par: błędy bez maski {pomiar['falszywe_bez_maski']}, "
                          f"z maską {pomiar['falszywe_z_maska']}")
        tabela.append("Wariant                     Trafienia   Czułość  Bez hashy")
        for nazwa_wariantu, liczby in sorted(hashe["warianty"].items()):
            trafione, wszystkie = liczby["trafione"], liczby["wszystkie"]
            tabela.append(f"{nazwa_wariantu:26} {trafione:4}/{wszystkie:<4} {trafione / wszystkie:8.1%}"
                          f" {liczby['bez_hashy']:9}")
    if wyglad is not None:
        tabela.append(f"Obrazy stron NASK: {wyglad['obrazy_nask']}; warianty: {wyglad['warianty']}; "
                      f"kreacje: {wyglad['kreacje']}; pary: {wyglad['pary']}")
        if wyglad["uwzglednij_szkice"]:
            tabela.append("Uwzględniono szkice dopuszczone do tego pomiaru.")
        tabela.append("Model   Tryb            Warianty  Elementy  Logo  Reklama Top 1  Szablon Top 1   Wektor ms   Wektor + wycinki ms")
        for model, tryby in wyglad["modele"].items():
            for tryb, liczby in tryby.items():
                trafione = metryki[f"{model}_{tryb}_top1"]
                el_tp = sum(w["kalibracja"]["tp"] for w in liczby["elementy"].values())
                el_fn = sum(w["kalibracja"]["fn"] for w in liczby["elementy"].values())
                logo = liczby["logo_lacznie"]["skalibrowane"]
                ranking = liczby["ranking_zatwierdzonych"]["ranking"]
                reka, szablon = ranking["ta_sama_reklama"], ranking["ten_sam_szablon"]
                tabela.append(f"{model:7} {tryb:14} {trafione:3}/{wyglad['warianty']:<3}    {el_tp:2}/{el_tp + el_fn:<2}    "
                              f"{logo['tp']}/{logo['tp'] + logo['fn']}       "
                              f"{reka['top1']}/{reka['zapytania']}            {szablon['top1']}/{szablon['zapytania']}      "
                              f"{liczby['czas_wektora_ms']:11.2f} {liczby['czas_z_wycinkami_ms']:16.2f}")
                tabela.append(f"  Próba LIVE: pasek {liczby['kontrola_syntetyczna']['pasek_tekst_dol']:.3f}, "
                              f"plakietka {liczby['kontrola_syntetyczna']['plakietka_na_zywo']:.3f}, "
                              f"marki po kalibracji {liczby['kontrola_syntetyczna']['marki_skalibrowane']}")
            tabela.extend(tabela_kreacji(model, tryby["dopelnienie"]))
    if twarze is not None:
        tabela.extend(tabela_twarzy(twarze))
    if jakosc_twarzy is not None:
        tabela.extend(tabela_magface(jakosc_twarzy))
    if facetorch is not None:
        tabela.extend(tabela_facetorch(facetorch))
    if qr is not None:
        tabela.append("Próba                   Odczyty    Fałszywe    Czas ms     Skuteczna metoda")
        for p in qr["proby"]:
            tabela.append(f"{p['nazwa']:23} {p['odczytano']}/{p['oczekiwano']:5} {p['falszywe']:9} "
                          f"{p['czas_ms']:10.2f}     {', '.join(p['metody']) or '—'}")
        tabela.append(f"Minimalny odczytany moduł kontrolny: {qr['minimalny_modul_px']} px")
        tabela.append(f"Kreacje NASK: {len(qr['nask'])}, odczytane kody: "
                      f"{sum(len(p['odczyty'] or []) for p in qr['nask'])}")
        for p in qr["nask"]:
            if p["odczyty"] is None:
                tabela.append(f"{p['id']}: brak obrazu do kontroli")
            for odczyt in p["odczyty"] or ():
                tabela.append(f"{p['id']}: {odczyt['tekst']!r}")
    if porownania is not None:
        tabela.extend(tabela_porownan(porownania))
        from fakecollector.features.image.raport_html import zapisz_raport_html

        zapisz_raport_html(katalog / f"{nazwa}.html", pary_html, "\n".join(tabela))
    tekst = "\n".join(tabela) + "\n"
    (katalog / f"{nazwa}.txt").write_text(tekst, encoding="utf-8")
    tymczasowy = katalog / f"{nazwa}.tmp"
    tymczasowy.write_text(json.dumps(raport, ensure_ascii=False, indent=2), encoding="utf-8")
    tymczasowy.replace(katalog / f"{nazwa}.json")
    print(tekst, end="")
    print(katalog / f"{nazwa}.json")


def tabela_porownan(raport: dict) -> list[str]:
    proba = raport["proba_generalna"]
    tabela = [f"Próba generalna: {proba['status']} {proba.get('powod', '')}",
              f"Odczytane klatki filmów NASK: {raport['klatki_odczytane']}",
              "ORB, niezależne sceny syntetyczne | Dowody / pary | Mediana ms"]
    for nazwa, w in raport["orb"].items():
        tabela.append(f"{nazwa} | {w['odczytane']}/{w['pary']} | {w['mediana_ms']:.1f}")
    for zbior, wyniki in (("Diagnostyka lokalna", raport["diagnostyka"]), ("Próba generalna", proba.get("wyniki"))):
        if wyniki is None:
            continue
        tabela.append(f"{zbior} | Rodzaj | Siła | Trafne | Fałszywe | Bez adnotacji | Precyzja | Czułość")
        for nazwa, w in wyniki["dowody"].items():
            precyzja = f"{w['precyzja']:.0%}" if w["precyzja"] is not None else "—"
            czulosc = f"{w['czulosc']:.0%}" if w["czulosc"] is not None else "—"
            tabela.append(f"{nazwa} | {w['sila']} | {w['trafne']} | {w['falszywe']} | {w['bez_adnotacji']} | {precyzja} | {czulosc}")
        tabela.append("Ablacja | Trafne pary | Fałszywe silne/średnie | Pary tylko ze słabym dowodem")
        for w in wyniki["ablacja"]:
            tabela.append(f"{w['etap']} | {w['pary_trafne']} | {w['pary_falszywe_silne_srednie']} | {w['pary_slabe']}")
    w = raport["wydajnosc"]
    if w["obrazy"]:
        tabela.append(f"CPU, {w['obrazy']} obrazów: tani {w['tani_na_s']:.2f} obrazów/s; pełny {w['pelny_na_s']:.2f} obrazów/s")
    return tabela


def tabela_magface(raport: dict) -> list[str]:
    tabela = ["Model | Degradacja | Ocena | Średnie ρ Spearmana | Twarze z określonym ρ"]
    for w in raport["korelacje"]:
        rho = "—" if w["rho"] is None else f"{w['rho']:.3f}"
        tabela.append(f"{w['model']} | {w['seria']} | {w['metoda']} | {rho} | {w['twarze']}")
    tabela.append("Zbiór | Filtr | Tryb rozpoznawania | Trafne | Błędne nazwiska | Niepewne")
    for w in raport["dopasowania"]:
        tabela.append(f"{w['zbior']} | {w['metoda']} | {w['model']} | {w['trafne']}/{w['proby']} | "
                      f"{w['falszywe']} | {w['niepewne']}")
    for metoda, w in raport["czas_ms"].items():
        tabela.append(f"{metoda}: CPU {w['mediana']:.3f} ms/twarz (mediana), {w['srednia']:.3f} ms (średnia)")
    k = raport["kalibracja_jakosci"]
    for s, ostrosc in k["ostrosc_dobrej"].items():
        tabela.append(f"Ostrość dobra {s} 112 × 112, P{k['percentyl_ostrosci_dobrej'][s]}: {ostrosc:.6f}")
    tabela.append(f"Progi składnika: {k['prog_skladnika']}; cała ocena: {k['calosc']} (P{k['percentyle_calosci']})")
    tabela.append("Wariant | Ocena | Rozmiar odciął | Jakość odcięła ≥ 1 głos (oba) | Trafne | Bez nazwiska | "
                  "Odcięte niepewne/chybione w mierze zastępczej z progami wejściowymi")
    for w in k["warianty"]:
        tabela.append(f"{w['wariant']} | {w['metoda']} | {w['odciete_rozmiarem']}/{w['probki']} | "
                      f"{w['odciete_jakoscia']} ({w['odciete_oba_jakoscia']}) | {w['trafne']} | {w['bez_nazwiska']} | "
                      f"{w['odciete_zastepcza_niepewne']}/{w['odciete_zastepcza_chybione']}")
    tabela.append("Kreacja | Twarze | MagFace CPU ms | Cały etap twarzy CPU ms")
    for w in raport["czasy_kreacji_ms"]:
        tabela.append(f"{w['id']} | {w['twarze']} | {w['magface_ms']:.2f} | {w['twarze_ms']:.2f}")
    return tabela


def tabela_facetorch(raport: dict) -> list[str]:
    tabela = ["Zbiór | Obrazy | Opisy/twarze | Deepfake min/P25/med/P75/max (orientacyjnie) | "
              "Emocje (orientacyjnie) | Identyczne powtórki | Śr. CPU ms/twarz | Śr. CPU ms/obraz | "
              "Med. CPU ms/obraz z opisem"]
    for nazwa, w in raport["zbiory"].items():
        rozklad = "/".join(f"{x:.3f}" for x in w["rozklad_deepfake"] or ()) or "—"
        emocje = ", ".join(f"{e} {n}" for e, n in sorted(w["emocje"].items())) or "—"
        czasy = ["—" if w[k] is None else f"{w[k]:.2f}" for k in
                 ("ms_na_twarz", "ms_na_obraz_srednia", "ms_na_obraz_z_opisem_mediana")]
        tabela.append(f"{nazwa} | {w['obrazy']} | {w['wyniki']}/{w['twarze']} | {rozklad} | {emocje} | "
                      f"{w['powtarzalne']}/{w['obrazy']} | {' | '.join(czasy)}")
        tabela.append(f"  Próg jakości: {w['przechodzi_prog']}/{w['twarze']}; "
                      f"z wektorem: {w['ma_wektor']}; z oboma wektorami: {w['ma_oba_wektory']}")
        if w["braki"] or w["obrazy_bez_twarzy"]:
            tabela.append(f"  Bez twarzy: {w['obrazy_bez_twarzy']}; braki opisów: {w['braki']}")
        if nazwa == "kreacje":
            tabela.extend(f"  {p['id']}: {p['facetorch_ms']:.2f} ms" for p in w["pomiary"])
        if nazwa == "portrety":
            for p in w["pomiary"]:
                opisy = [f"{t['deepfake']:.3f}, {t['emocja']['etykieta']} ({t['emocja']['pewnosc']:.3f})"
                         if t["deepfake"] is not None else t["brak"] for t in p["twarze"]]
                tabela.append(f"  {p['id']} (orientacyjnie): {', '.join(opisy) or 'brak wykrytej twarzy'}")
    return tabela


def tabela_kreacji(model: str, liczby: dict) -> list[str]:
    def procent(wartosc: float | None) -> str:
        return "—" if wartosc is None else f"{wartosc:.0%}"

    tabela = [f"\n{model}, dopełnienie. Próg, precyzja/czułość po kalibracji, TP/FP/FN, "
              "precyzja/czułość poza zgłoszeniem."]
    for grupa in ("elementy", "logo"):
        tabela.append(grupa)
        for nazwa, wpis in liczby[grupa].items():
            prog = "—" if wpis["prog"] is None else f"{wpis['prog']:.6f}"
            wynik, poza = wpis["kalibracja"], wpis["poza_proba"]
            tabela.append(f"{nazwa:25} {prog:>10}  {procent(wynik['precyzja']):>4}/{procent(wynik['czulosc']):4} "
                          f"  {wynik['tp']}/{wynik['fp']}/{wynik['fn']} "
                          f"  {procent(poza['precyzja']):>4}/{procent(poza['czulosc']):4}")
    for w in liczby["logo_kreacje"]:
        tabela.append(f"{w['id']}: oczekiwane {w['oczekiwane']}, wykryte {w['skalibrowane']}")
    ranking = liczby["ranking_zatwierdzonych"]
    tabela.append(f"T_w: {ranking['T_w']}, maksimum par różnych: {ranking['maks_cosinus_rozne']}")
    tabela.append(f"Pary różne z wektorem: {ranking['pary_rozne_z_wektorem']}, "
                  f"bez wektora: {ranking['pary_rozne_bez_wektora']}")
    for w in ranking["pary"]:
        cosinus = "—" if w["cosinus"] is None else f"{w['cosinus']:.6f}"
        tabela.append(f"{w['a']} ↔ {w['b']}  {w['relacja']:17} cos {cosinus:>8}  rangi {w['rangi']}")
    return tabela


def tabela_twarzy(raport: dict) -> list[str]:
    tabela = ["Zgodna liczba, średni błąd bezwzględny liczby twarzy, średni czas, dopasowania, fałszywe dopasowania"]
    for model, m in raport["modele"].items():
        kalibracja = m["kalibracja"]
        znane = f"{kalibracja['trafne']}/{kalibracja['proby_znanych']}" if kalibracja else "—"
        bledy = kalibracja["falszywe_dopasowania"] if kalibracja else "—"
        mae = f"{m['mae']:.2f}" if m["mae"] is not None else "—"
        czas = f"{m['sredni_czas_ms']:.2f} ms" if m["sredni_czas_ms"] is not None else "—"
        tabela.append(f"{model}: {m['zgodne_liczby']}/{len(m['wykrycie'])} | {mae} | {czas} | {znane} | {bledy}")
        for w in m["wykrycie"]:
            tabela.append(f"  {w['id']}: {w['wykryte']}/{w['po_limicie']} twarzy, "
                          f"{w['uzyteczne_wektory']} wektorów, {w['czas_ms']:.2f} ms")
        if kalibracja:
            tabela.append(f"  T_osoba={kalibracja['T_osoba']}; margines={kalibracja['margines']}; T_f={kalibracja['T_f']}")
            for w in kalibracja["macierz"]:
                tabela.append(f"  {w['prawda']} → {w['decyzja']}: {w['liczba']}")
        test = m["zdjecia_testowe"]
        if test:
            tabela.append(f"  Odłożone zdjęcia: {test['trafne']}/{test['zdjecia']}, "
                          f"błędne nazwiska: {test['pomylki']}, zdjęcia grupowe: {test['zdjecia_grupowe']}")
            for w in test["macierz"]:
                tabela.append(f"  {w['prawda']} → {w['decyzja']}: {w['liczba']}")
        for k in m["kontrole_nieznanych"]:
            wynik_kontroli = ", ".join(k["nazwiska"]) or ("nieznana" if k["wektory"] else "brak użytecznego wektora")
            tabela.append(f"  Kontrola {k['id']}: {wynik_kontroli}, wektory: {k['wektory']}")
    if (oba := raport.get("laczony")) is not None:
        tabela.append("Głosy modeli | wyjmij jedno | błędne nazwiska | niepewne | symulowane obce odrzucone")
        for nazwa, w in {**oba["osobno"], "oba": oba}.items():
            z, s = w["znane"], w["symulowane"]
            tabela.append(f"{nazwa} | {z['trafne']}/{z['proby']} | {z['bledne_nazwiska']} | "
                          f"{z['niepewne']} | {s['nieznane']}/{s['proby']}")
        for k in oba["kontrole"]:
            tabela.append(f"  Kontrola łączona {k['id']}: {', '.join(w['status'] for w in k['wyniki']) or 'brak twarzy'}")
    return tabela


if __name__ == "__main__":
    main()
