"""Desdobra uma base de metas sem alterar seus totais originais."""
import argparse
import calendar
import sqlite3
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path


def proportional(total, weights):
    denominator = sum(weights.values())
    shares = {key: total * weight // denominator for key, weight in weights.items()}
    remaining = total - sum(shares.values())
    order = sorted(weights, key=lambda key: (-(total * weights[key] % denominator), key))
    for key in order[:remaining]:
        shares[key] += 1
    return shares


def distribute(sales_path, targets_path, output_path):
    if output_path.exists():
        raise ValueError("o destino já existe")
    with sqlite3.connect(sales_path.resolve().as_uri() + "?mode=ro", uri=True) as sales:
        latest = date.fromisoformat(sales.execute(
            "SELECT MAX(data_emissao) FROM fato_item_orcamento"
        ).fetchone()[0])
        end = date(latest.year, latest.month, 1)
        if latest.day == calendar.monthrange(latest.year, latest.month)[1]:
            end = date(latest.year + latest.month // 12, latest.month % 12 + 1, 1)
        start = date(end.year - 1, end.month, 1)
        history = sales.execute("""
            SELECT id_unidade, id_servico, COALESCE(id_cliente,''), COUNT(*)
            FROM fato_item_orcamento WHERE data_emissao>=? AND data_emissao<?
            GROUP BY 1,2,3
        """, (start.isoformat(), end.isoformat())).fetchall()
        weights = defaultdict(Counter)
        unit_weights = defaultdict(Counter)
        for unit, product, customer, quantity in history:
            weights[unit, product][customer] += quantity
            unit_weights[unit][customer] += quantity
        sellers = dict(sales.execute("""
            SELECT customer, id_vendedor FROM (
                SELECT COALESCE(id_cliente,'') customer, id_vendedor,
                ROW_NUMBER() OVER (
                    PARTITION BY COALESCE(id_cliente,'')
                    ORDER BY data_emissao DESC, id_item_erp DESC
                ) position FROM fato_item_orcamento WHERE data_emissao<?
            ) WHERE position=1
        """, (end.isoformat(),)))
    with sqlite3.connect(targets_path.resolve().as_uri() + "?mode=ro", uri=True) as source:
        with sqlite3.connect(output_path) as output:
            source.backup(output)
            output.executescript("""
                CREATE TABLE meta_cliente (
                    ano_mes TEXT NOT NULL, id_unidade TEXT NOT NULL,
                    id_servico TEXT NOT NULL, id_vistoriador TEXT NOT NULL,
                    id_cliente TEXT NOT NULL, id_vendedor TEXT,
                    quantidade_milionesimos INTEGER NOT NULL CHECK(quantidade_milionesimos>0),
                    faturamento_centavos INTEGER NOT NULL CHECK(faturamento_centavos>=0),
                    PRIMARY KEY(ano_mes,id_unidade,id_servico,id_vistoriador,id_cliente)
                );
                CREATE TABLE meta_cliente_vendedor (
                    id_cliente TEXT PRIMARY KEY, id_vendedor TEXT
                );
                CREATE TABLE meta_distribuicao (
                    inicio_historico TEXT, fim_exclusivo_historico TEXT,
                    criterio TEXT
                );
            """)
            output.executemany("INSERT INTO meta_cliente_vendedor VALUES (?,?)", sellers.items())
            output.execute("INSERT INTO meta_distribuicao VALUES (?,?,?)", (
                start.isoformat(), end.isoformat(),
                "Participação em quantidade por produto nos últimos 12 meses completos; "
                "fallback por unidade. Vendedor do último lançamento do cliente, "
                "desempate por id do item. Quantidade em milionésimos, receita em centavos."
            ))
            for month, unit, product, inspector, quantity, revenue in source.execute(
                "SELECT * FROM meta_produto_vistoriador ORDER BY 1,2,3,4"
            ):
                participation = weights[unit, product] or unit_weights[unit]
                if not participation:
                    raise ValueError("unidade sem histórico para distribuir")
                quantities = proportional(quantity * 1000000, participation)
                revenues = proportional(revenue, participation)
                output.executemany("INSERT INTO meta_cliente VALUES (?,?,?,?,?,?,?,?)", [
                    (month, unit, product, inspector, customer, sellers.get(customer),
                     quantities[customer], revenues[customer])
                    for customer in participation if quantities[customer]
                ])
            output.commit()
            print(output.execute("SELECT COUNT(*),COUNT(DISTINCT id_cliente) FROM meta_cliente").fetchone())
            print(start, end)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("vendas", type=Path)
    parser.add_argument("metas", type=Path)
    parser.add_argument("destino", type=Path)
    args = parser.parse_args()
    distribute(args.vendas, args.metas, args.destino)
