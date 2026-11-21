"""Graf powiązań: kampanie, operatorzy, materiały i wspólne ślady (domeny, osoby, konta, kit, serwery).

Graf powstaje z danych w Postgresie (rekordy, krawędzie z grupowania, klucze), więc działa także
bez Neo4j. Format odpowiedzi pasuje do bibliotek grafowych (Cytoscape.js, vis-network,
react-force-graph):

    {"nodes": [{"id": "material:ab12", "type": "material", "label": "…", "group": "campaign:3", "data": {…}}],
     "edges": [{"id": "…", "source": "material:ab12", "target": "campaign:3", "type": "w_kampanii",
                "label": "…", "weight": 0.93, "data": {"reasons": […]}}],
     "stats": {…}, "legend": {…}}

Typy węzłów:  operator, campaign, material, domain, person, account, kit, infra
Typy krawędzi: w_kampanii (materiał→kampania), operator (kampania→operator),
               ta_sama_kampania / do_weryfikacji / ten_sam_operator (materiał–materiał, z przesłankami),
               uzywa (materiał→domena/konto/kit/serwer), wymienia / pokazuje (materiał→osoba)
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from fakecollector.grouping.compare import ASSIGN, CLONE_PRONE, REVIEW, load_context

# rodzaj klucza rekordu -> typ węzła grafu
ENTITY_KIND: dict[str, str] = {"domain": "domain", "person": "person", "person_visual": "person",
                               "phone": "account", "email": "account"}
for k in ("fb_pixel", "google_ads", "ga4", "gtm", "ua", "tiktok_pixel", "yandex_metrika", "clarity", "hotjar",
          "snap_pixel", "taboola", "outbrain", "adsense", "google_site_verification", "fb_domain_verification",
          "yandex_verification", "fb_page_id", "fb_ad_object", "phone", "email", "messenger", "crypto_wallet",
          "secret_param", "registrant_org", "ad_naming"):
    ENTITY_KIND[f"osint:{k}"] = "account"
for k in ("kit_dir", "js_hash", "css_hash", "inline_js_hash", "endpoint", "favicon_mmh3", "form_signature",
          "actor_host", "media_host", "image_hash"):
    ENTITY_KIND[f"osint:{k}"] = "kit"
for k in ("ip", "cidr", "ns_set", "mx", "cert_sha256", "cert_san_peer", "registration_batch", "soa_rname",
          "spf_include"):
    ENTITY_KIND[f"osint:{k}"] = "infra"

KIND_PL = {
    "domain": "domena", "person": "osoba w tekście", "person_visual": "osoba na obrazie", "phone": "telefon",
    "email": "e-mail", "fb_pixel": "Pixel Meta", "google_ads": "konto Google Ads", "ga4": "Google Analytics 4",
    "gtm": "Google Tag Manager", "ua": "Universal Analytics", "tiktok_pixel": "Pixel TikTok",
    "fb_page_id": "strona reklamodawcy Meta", "fb_ad_object": "obiekt Meta Ads", "messenger": "komunikator",
    "crypto_wallet": "portfel kryptowalut", "kit_dir": "katalog wdrożenia kitu", "js_hash": "skrypt JS",
    "css_hash": "arkusz CSS", "inline_js_hash": "skrypt w stronie", "endpoint": "endpoint/beacon",
    "favicon_mmh3": "favicona", "form_signature": "formularz", "actor_host": "serwer aktora",
    "media_host": "serwer wideo", "image_hash": "plik obrazu", "ip": "adres IP", "cidr": "sieć",
    "ns_set": "serwery DNS", "mx": "serwer poczty", "cert_sha256": "certyfikat TLS",
    "cert_san_peer": "wspólny certyfikat", "registration_batch": "rejestracja hurtowa",
}

LEGEND = {
    "nodes": {"operator": "grupa kampanii z tą samą infrastrukturą (ten sam operator)",
              "campaign": "kampania: materiały o tej samej treści",
              "material": "zgłoszony materiał (reklama, strona, nagranie)",
              "domain": "domena strony", "person": "osoba wykorzystana w reklamie",
              "account": "identyfikator konta: piksel, analityka, telefon, portfel",
              "kit": "wspólny element zestawu do budowy stron (skrypt, katalog, serwer aktora)",
              "infra": "wspólna infrastruktura sieciowa (IP, DNS, certyfikat)"},
    "edges": {"w_kampanii": "materiał należy do kampanii", "operator": "kampania należy do operatora",
              "ta_sama_kampania": "pewne powiązanie treści (≥ 0.75)",
              "do_weryfikacji": "podobne, decyzja analityka (0.5–0.75)",
              "ten_sam_operator": "wspólna infrastruktura, różna treść",
              "uzywa": "materiał używa domeny / konta / elementu kitu / serwera",
              "wymienia": "osoba w tekście", "pokazuje": "osoba rozpoznana na obrazie"},
}

MAX_ENTITY_NODES = 400


def _short(value: str, n: int = 40) -> str:
    value = str(value)
    return value if len(value) <= n else value[:n - 1] + "…"


def build_graph(campaign_id: int | None = None, operator_group: int | None = None,
                material_id: str | None = None, entities: bool = True, shared_only: bool = True,
                min_score: float = REVIEW) -> dict[str, Any]:
    """Graf całej bazy albo jego wycinek: jedna kampania, jeden operator albo otoczenie materiału."""
    from fakecollector.grouping.campaigns import connect

    with connect() as conn:
        # ---- wybór materiałów
        if campaign_id is not None:
            ids = [r[0] for r in conn.execute("SELECT id FROM materials WHERE campaign_id = %s",
                                              (campaign_id,)).fetchall()]
        elif operator_group is not None:
            ids = [r[0] for r in conn.execute(
                """SELECT m.id FROM materials m JOIN campaigns c ON c.id = m.campaign_id
                   WHERE c.operator_group = %s OR c.id = %s""", (operator_group, operator_group)).fetchall()]
        elif material_id is not None:
            row = conn.execute("SELECT campaign_id FROM materials WHERE id = %s", (material_id,)).fetchone()
            if not row:
                return {"nodes": [], "edges": [], "stats": {}, "legend": LEGEND}
            ids = {material_id}
            if row[0] is not None:
                ids |= {r[0] for r in conn.execute("SELECT id FROM materials WHERE campaign_id = %s",
                                                   (row[0],)).fetchall()}
            ids |= {r[0] for r in conn.execute(
                """SELECT CASE WHEN a = %s THEN b ELSE a END FROM material_links
                   WHERE (a = %s OR b = %s) AND (score >= %s OR operator_score >= %s)""",
                (material_id, material_id, material_id, min_score, ASSIGN)).fetchall()}
            ids = list(ids)
        else:
            ids = [r[0] for r in conn.execute("SELECT id FROM materials").fetchall()]
        if not ids:
            return {"nodes": [], "edges": [], "stats": {"materials": 0}, "legend": LEGEND}

        mats = conn.execute(
            """SELECT id, source, title, domain, status, campaign_id, assignment, assignment_score, persons,
                      persons_visual, input_type, created_at, n_words, text_origin
               FROM materials WHERE id = ANY(%s)""", (ids,)).fetchall()
        camp_ids = sorted({m[5] for m in mats if m[5] is not None})
        camps = conn.execute("""SELECT id, label, summary, size, operator_group, domains, persons
                                FROM campaigns WHERE id = ANY(%s)""", (camp_ids,)).fetchall() if camp_ids else []
        links = conn.execute(
            """SELECT a, b, score, decision, reasons, operator_score, operator_reasons FROM material_links
               WHERE a = ANY(%s) AND b = ANY(%s) AND (score >= %s OR operator_score >= %s)""",
            (ids, ids, min_score, ASSIGN)).fetchall()
        keys = conn.execute("SELECT material_id, kind, value FROM material_keys "
                            "WHERE material_id = ANY(%s) AND kind = ANY(%s)",
                            (ids, list(ENTITY_KIND))).fetchall() if entities else []

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []

    def node(nid: str, ntype: str, label: str, **data: Any) -> None:
        if nid not in nodes:
            nodes[nid] = {"id": nid, "type": ntype, "label": label, "data": data}

    def edge(src: str, dst: str, etype: str, label: str = "", weight: float | None = None, **data: Any) -> None:
        edges.append({"id": f"{etype}:{src}->{dst}", "source": src, "target": dst, "type": etype,
                      "label": label, "weight": weight, "data": data})

    # ---- operatorzy i kampanie
    for cid, label, summary, size, op, domains, persons in camps:
        node(f"campaign:{cid}", "campaign", label or f"kampania {cid}", campaign_id=cid, summary=summary,
             size=size, operator_group=op, domains=domains, persons=persons)
        if op is not None:
            node(f"operator:{op}", "operator", f"Operator {op}", operator_group=op)
            edge(f"campaign:{cid}", f"operator:{op}", "operator", "ten sam operator")

    # ---- materiały
    for (mid, source, title, domain, status, cid, assignment, ascore, persons, pvis, itype, created, nw,
         origin) in mats:
        node(f"material:{mid}", "material", _short(title or source or mid, 50), material_id=mid, source=source,
             title=title, domain=domain, status=status, assignment=assignment, score=ascore,
             input_type=itype, created_at=created.isoformat() if created else None, n_words=nw,
             text_origin=origin)
        if cid is not None:
            nodes[f"material:{mid}"]["group"] = f"campaign:{cid}"
            edge(f"material:{mid}", f"campaign:{cid}", "w_kampanii", assignment or "", ascore)

    # ---- powiązania materiał–materiał (z przesłankami)
    for a, b, score, decision, reasons, op_score, op_reasons in links:
        if score >= ASSIGN:
            edge(f"material:{a}", f"material:{b}", "ta_sama_kampania", f"{score:.2f}", score, reasons=reasons)
        elif score >= min_score:
            edge(f"material:{a}", f"material:{b}", "do_weryfikacji", f"{score:.2f}", score, reasons=reasons)
        elif op_score >= ASSIGN:
            edge(f"material:{a}", f"material:{b}", "ten_sam_operator", f"{op_score:.2f}", op_score,
                 reasons=op_reasons)

    # ---- wspólne ślady
    if entities and keys:
        ctx = load_context()
        users: dict[tuple[str, str], set[str]] = defaultdict(set)
        for mid, kind, value in keys:
            users[(kind, value)].add(mid)
        ranked = sorted(users.items(), key=lambda kv: -len(kv[1]))
        added = 0
        for (kind, value), mids in ranked:
            short_kind = kind.split(":", 1)[-1]
            if shared_only and len(mids) < 2 and kind != "domain":
                continue
            if short_kind in CLONE_PRONE and ctx.ubiquitous(ctx.osint_df.get((short_kind, value), 0)):
                continue        # szablon podszywanego serwisu – w grafie byłby tylko szumem
            if added >= MAX_ENTITY_NODES:
                break
            ntype = ENTITY_KIND[kind]
            nid = f"{ntype}:{short_kind}:{value}" if ntype not in {"domain", "person"} else f"{ntype}:{value}"
            label = _short(value, 32) if ntype in {"domain", "person"} else f"{KIND_PL.get(short_kind, short_kind)}: {_short(value, 24)}"
            node(nid, ntype, label, kind=KIND_PL.get(short_kind, short_kind), value=value, materials=len(mids))
            added += 1
            etype = "wymienia" if kind == "person" else "pokazuje" if kind == "person_visual" else "uzywa"
            for mid in mids:
                edge(f"material:{mid}", nid, etype, KIND_PL.get(short_kind, short_kind))

    counts = Counter(n["type"] for n in nodes.values())
    ecounts = Counter(e["type"] for e in edges)
    return {"nodes": list(nodes.values()), "edges": edges,
            "stats": {"nodes": len(nodes), "edges": len(edges), "by_node_type": dict(counts),
                      "by_edge_type": dict(ecounts)},
            "legend": LEGEND}
