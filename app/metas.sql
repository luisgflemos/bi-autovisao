CREATE TABLE IF NOT EXISTS meta_mensal (
    ano_mes TEXT NOT NULL,
    nivel TEXT NOT NULL CHECK (nivel IN ('unidade', 'vendedor')),
    nome TEXT NOT NULL,
    grupo TEXT NOT NULL CHECK (grupo IN ('Transferência', 'Cautelar', 'Outros')),
    quantidade INTEGER NOT NULL CHECK (quantidade >= 0),
    preco_medio_centavos INTEGER NOT NULL CHECK (preco_medio_centavos >= 0),
    PRIMARY KEY (ano_mes, nivel, nome, grupo)
);
