"""Harmonogram skanera Biblioteki Reklam (usługa `scheduler` w docker-compose).

    python -m fakecollector.worker            # skan co kilka godzin + zadania dobowe
    python -m fakecollector.worker --raz      # jeden skan i koniec
    python -m fakecollector.worker --ponow    # ponowna analiza reklam, których wynik nie trafił do kartoteki
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.blocking import BlockingScheduler

from fakecollector.adlib import Skaner
from fakecollector.adlib.model import FiltrTokenu

log = logging.getLogger("fakecollector.worker")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Skaner Biblioteki Reklam Meta")
    p.add_argument("--raz", action="store_true", help="jeden skan i koniec")
    p.add_argument("--fraza", help="szukaj tylko tej frazy (z --raz)")
    p.add_argument("--limit", type=int, help="najwyżej tyle reklam do analizy")
    p.add_argument("--ponow", action="store_true",
                   help="przeanalizuj ponownie reklamy bez rekordu w kartotece (np. sprzed zapisu materiałów) i koniec")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    for handler in logging.getLogger().handlers:
        handler.addFilter(FiltrTokenu())

    skaner = Skaner.z_srodowiska()
    skaner.odswiez_cert()
    if args.ponow:
        print(json.dumps(skaner.ponow_analize(args.limit), ensure_ascii=False, indent=1, default=str))
        return
    if args.raz:
        print(json.dumps(skaner.skanuj("reczny", args.fraza, args.limit), ensure_ascii=False, indent=1, default=str))
        return

    h = skaner.cfg["harmonogram"]
    plan = BlockingScheduler(timezone="Europe/Warsaw")
    # Czas ze strefą: kontener działa w UTC, a harmonogram w czasie polskim.
    teraz = datetime.now(timezone.utc)
    start = teraz + timedelta(seconds=30) if h["skan_przy_starcie"] else None
    wspolne = {"max_instances": 1, "coalesce": True, "misfire_grace_time": 600}
    plan.add_job(skaner.skanuj, "interval", hours=h["co_godzin"], next_run_time=start, id="skan", **wspolne)
    plan.add_job(_dobowe, "interval", hours=h["sprawdzanie_co_godzin"], args=[skaner],
                 next_run_time=teraz + timedelta(minutes=10), id="dobowe", **wspolne)
    log.info("Harmonogram: skan co %s h, tryb %s", h["co_godzin"], skaner.klient.tryb)
    plan.start()


def _dobowe(skaner: Skaner) -> None:
    skaner.odswiez_cert()
    log.info("Sprawdzenie zdjętych reklam: %s", skaner.sprawdz_zdjete())


if __name__ == "__main__":
    main()
