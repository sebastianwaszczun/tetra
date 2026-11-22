-- Uruchamiany automatycznie przy pierwszym starcie bazy (pusty wolumen pgdata).
-- Tabele systemu tworzy kod przy pierwszym połączeniu:
--   materials, material_features, material_vectors, material_keys   – fakecollector.record.store
--   material_links, material_decisions, rozszerzenie campaigns       – fakecollector.grouping.campaigns
--   adlib_reklamy, adlib_tropy, adlib_skany                          – fakecollector.adlib.magazyn
CREATE EXTENSION IF NOT EXISTS vector;

-- Kampanie (spójne składowe grafu powiązań); kolumny opisu i operatora dokłada grupowanie.
CREATE TABLE IF NOT EXISTS campaigns (
    id            BIGSERIAL PRIMARY KEY,
    label         TEXT,
    method        TEXT,
    summary       TEXT,
    created_at    TIMESTAMPTZ DEFAULT now()
);
