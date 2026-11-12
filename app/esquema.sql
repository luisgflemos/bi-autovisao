-- Executar em uma base SQLite dedicada ao BI, com foreign_keys habilitado
-- também em cada conexão que carregar os dados.
PRAGMA foreign_keys = ON;
BEGIN;

CREATE TABLE IF NOT EXISTS dim_data (
    data TEXT PRIMARY KEY NOT NULL -- Data de emissão em ISO 8601 (AAAA-MM-DD).
);

CREATE TABLE IF NOT EXISTS dim_unidade (
    id_erp TEXT PRIMARY KEY NOT NULL,
    nome TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_servico (
    id_erp TEXT PRIMARY KEY NOT NULL,
    nome TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_vendedor (
    id_erp TEXT PRIMARY KEY NOT NULL,
    nome TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_cliente (
    id_erp TEXT PRIMARY KEY NOT NULL,
    nome_fantasia TEXT,
    periodicidade TEXT,
    agrupamento TEXT,
    segmento TEXT,
    macro_regiao TEXT
);

CREATE TABLE IF NOT EXISTS dim_vistoriador (
    id_erp TEXT PRIMARY KEY NOT NULL,
    nome TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dim_condicao_pagamento (
    id_erp TEXT PRIMARY KEY NOT NULL,
    descricao TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fato_item_orcamento (
    id_item_erp TEXT PRIMARY KEY NOT NULL, -- item_orc_id na API, SC6_ID no SQL.
    id_orcamento_erp TEXT NOT NULL,
    numero_orcamento TEXT,
    data_emissao TEXT NOT NULL REFERENCES dim_data(data),
    id_unidade TEXT NOT NULL REFERENCES dim_unidade(id_erp),
    id_servico TEXT NOT NULL REFERENCES dim_servico(id_erp),
    id_vendedor TEXT REFERENCES dim_vendedor(id_erp),
    id_cliente TEXT REFERENCES dim_cliente(id_erp),
    id_vistoriador TEXT REFERENCES dim_vistoriador(id_erp),
    id_condicao_pagamento TEXT REFERENCES dim_condicao_pagamento(id_erp),
    tipo_veiculo TEXT,
    marca_veiculo TEXT,
    modelo_veiculo TEXT,
    vistoria_movel INTEGER CHECK (vistoria_movel IN (0, 1)),
    -- NULL indica que o faturamento no nível do item ainda não foi apurado.
    item_faturado INTEGER CHECK (item_faturado IN (0, 1)),
    quantidade REAL NOT NULL,
    -- Valores totais do item em centavos. O preço médio é calculado na consulta.
    desconto_centavos INTEGER,
    valor_liquido_centavos INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fato_item_orcamento_emissao_unidade
    ON fato_item_orcamento(data_emissao, id_unidade);
CREATE INDEX IF NOT EXISTS idx_fato_item_orcamento_orcamento
    ON fato_item_orcamento(id_orcamento_erp);

COMMIT;
