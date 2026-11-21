"""Porównanie materiałów i grupowanie w kampanie.

    from fakecollector.grouping import assign_material, rebuild, compare_materials, list_campaigns
    assign_material(material_id)     # nowy materiał -> przypisany / do_weryfikacji / nowy_wzorzec
    rebuild()                        # przeliczenie kampanii dla całej bazy
    compare_materials(a, b)          # wynik pary z modułów i przesłanki

Progi: ASSIGN = 0.75 (ta sama kampania), REVIEW = 0.5 (do weryfikacji przez człowieka).
Kampania = ta sama treść (tekst, obraz, nagranie, domena). Operator = wspólna infrastruktura
(piksel, kit, serwery) – łączy różne kampanie relacją „ten sam operator”, ale ich nie scala.
"""
from .campaigns import (assign_material, get_campaign, links_of, list_campaigns, rebuild,
                        refresh_campaign, refresh_operators)
from .compare import (ASSIGN, MODULES, REVIEW, Context, PairResult, compare_features, compare_materials,
                      load_context)

__all__ = ["assign_material", "rebuild", "compare_materials", "compare_features", "list_campaigns",
           "get_campaign", "links_of", "refresh_campaign", "refresh_operators", "load_context", "Context",
           "PairResult", "ASSIGN", "REVIEW", "MODULES"]
