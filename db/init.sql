-- Uruchamiany automatycznie przy pierwszym starcie bazy (pusty wolumen pgdata).
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Pojedyncza zgłoszona reklama / strona docelowa
CREATE TABLE IF NOT EXISTS ads (
    id            BIGSERIAL PRIMARY KEY,
    source        TEXT,                 -- np. 'BACKUP/p7', 'scam_links.enc'
    landing_url   TEXT,
    final_url     TEXT,                 -- po przekierowaniach
    domain        TEXT,
    title         TEXT,
    ocr_text      TEXT,
    page_text     TEXT,
    screenshot    TEXT,                 -- ścieżka do pliku
    phash         TEXT,                 -- perceptual hash (hex)
    minhash_sig   BYTEA,
    created_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE (landing_url)
);

-- Twarze wykryte w reklamie (InsightFace + MagFace + facetorch)
CREATE TABLE IF NOT EXISTS faces (
    id            BIGSERIAL PRIMARY KEY,
    ad_id         BIGINT REFERENCES ads(id) ON DELETE CASCADE,
    bbox          REAL[],
    embedding     vector(512),          -- ArcFace
    quality       REAL,                 -- MagFace (norma wektora) – waga dopasowania
    deepfake_score REAL,                -- facetorch
    emotion       TEXT,
    matched_person TEXT,                -- dopasowanie do bazy referencyjnej
    match_score   REAL
);

-- Embeddingi całej kreacji / tekstu / głosu
CREATE TABLE IF NOT EXISTS ad_embeddings (
    ad_id         BIGINT PRIMARY KEY REFERENCES ads(id) ON DELETE CASCADE,
    clip          vector(512),          -- CLIP ViT-B/32
    siglip        vector(768),          -- SigLIP base
    text_emb      vector(384),          -- paraphrase-multilingual-MiniLM-L12-v2
    voice         vector(192)           -- SpeechBrain ECAPA
);

-- Infrastruktura
CREATE TABLE IF NOT EXISTS infra (
    id            BIGSERIAL PRIMARY KEY,
    ad_id         BIGINT REFERENCES ads(id) ON DELETE CASCADE,
    kind          TEXT,                 -- 'redirect','ip','ns','registrar','cert_issuer','favicon_mmh3','tracker','script_hash','pixel_id','gclid_campaign'
    value         TEXT,
    position      INT,                  -- kolejność w łańcuchu przekierowań
    observed_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS infra_kind_value ON infra(kind, value);

-- Powiązania między reklamami z uzasadnieniem (lustrzane do Neo4j)
CREATE TABLE IF NOT EXISTS links (
    ad_a          BIGINT REFERENCES ads(id) ON DELETE CASCADE,
    ad_b          BIGINT REFERENCES ads(id) ON DELETE CASCADE,
    signal        TEXT,                 -- 'face','phash','clip','text','minhash','voice','infra:...'
    score         REAL,
    weight        REAL,
    reason        TEXT,                 -- czytelne uzasadnienie krawędzi
    PRIMARY KEY (ad_a, ad_b, signal)
);

-- Kampanie (wynik HDBSCAN / spójnych składowych grafu)
CREATE TABLE IF NOT EXISTS campaigns (
    id            BIGSERIAL PRIMARY KEY,
    label         TEXT,
    method        TEXT,
    summary       TEXT,
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS campaign_ads (
    campaign_id   BIGINT REFERENCES campaigns(id) ON DELETE CASCADE,
    ad_id         BIGINT REFERENCES ads(id) ON DELETE CASCADE,
    PRIMARY KEY (campaign_id, ad_id)
);

-- Baza referencyjna wizerunków często podszywanych osób
CREATE TABLE IF NOT EXISTS reference_people (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    embedding     vector(512),
    voice         vector(192),
    note          TEXT
);

-- Indeksy wektorowe (HNSW, cosine)
CREATE INDEX IF NOT EXISTS faces_emb_hnsw   ON faces          USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS ref_emb_hnsw     ON reference_people USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS clip_hnsw        ON ad_embeddings  USING hnsw (clip vector_cosine_ops);
CREATE INDEX IF NOT EXISTS siglip_hnsw      ON ad_embeddings  USING hnsw (siglip vector_cosine_ops);
CREATE INDEX IF NOT EXISTS text_hnsw        ON ad_embeddings  USING hnsw (text_emb vector_cosine_ops);
CREATE INDEX IF NOT EXISTS voice_hnsw       ON ad_embeddings  USING hnsw (voice vector_cosine_ops);
CREATE INDEX IF NOT EXISTS ads_text_trgm    ON ads USING gin (page_text gin_trgm_ops);
