import calendar
import json
import os
import sqlite3
from contextlib import closing
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
PORT = int(os.environ.get("BI_PORT", "8765"))
HOST = os.environ.get("BI_HOST", "127.0.0.1")
DATABASE = Path(os.environ.get("BI_DATABASE", ROOT / "vendas.sqlite3")).resolve()
JOINS = """
FROM fato_item_orcamento f
JOIN dim_unidade u ON u.id_erp=f.id_unidade
JOIN dim_servico s ON s.id_erp=f.id_servico
LEFT JOIN dim_vendedor v ON v.id_erp=f.id_vendedor
LEFT JOIN dim_cliente c ON c.id_erp=f.id_cliente
"""
DIMENSIONS = {
    "unidade": "CASE WHEN TRIM(u.nome) IN ('', 'Sem unidade na exportacao') "
               "THEN 'Andrade Neves' ELSE u.nome END",
    "servico": "s.nome",
    "vendedor": "COALESCE(NULLIF(TRIM(v.nome), ''), 'Corporativo')",
    "grupo": "CASE WHEN LOWER(s.nome) LIKE '%cautelar%' THEN 'Cautelar' "
             "WHEN LOWER(s.nome) LIKE '%transfer%ncia%' THEN 'Transferência' ELSE 'Outros' END",
    "segmento": "COALESCE(NULLIF(TRIM(c.segmento), ''), 'Sem informação')",
    "cliente": "COALESCE(NULLIF(TRIM(c.nome_fantasia), ''), 'Sem informação')",
}
MEASURES = "COUNT(*), COALESCE(SUM(f.valor_liquido_centavos),0)"


def maintain_calendar(path):
    with closing(sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)) as db:
        latest = db.execute("SELECT MAX(data_emissao) FROM fato_item_orcamento").fetchone()[0]
        covered = db.execute("SELECT MAX(data) FROM dim_calendario_campinas").fetchone()[0]
        if not latest or latest <= covered:
            return
        holidays = {(1, 1): "Confraternização Universal", (4, 21): "Tiradentes",
                    (5, 1): "Dia do Trabalho", (7, 9): "Revolução Constitucionalista",
                    (9, 7): "Independência", (10, 12): "Nossa Senhora Aparecida",
                    (11, 2): "Finados", (11, 15): "Proclamação da República",
                    (11, 20): "Consciência Negra", (12, 8): "Nossa Senhora da Conceição",
                    (12, 25): "Natal"}
        last = date.fromisoformat(latest)
        end = date(last.year, last.month, calendar.monthrange(last.year, last.month)[1])
        rows = []
        day = date.fromisoformat(covered) + timedelta(days=1)
        for year in range(day.year, end.year + 1):
            # Computus gregoriano de Meeus/Jones/Butcher.
            a, (b, c) = year % 19, divmod(year, 100)
            d, e = divmod(b, 4)
            f = (b + 8) // 25
            g = (b - f + 1) // 3
            h = (19 * a + b - d - g + 15) % 30
            i, k = divmod(c, 4)
            l = (32 + 2 * e + 2 * i - h - k) % 7
            m = (a + 11 * h + 22 * l) // 451
            month, offset = divmod(h + l - 7 * m + 114, 31)
            easter = date(year, month, offset + 1)
            movable = {easter - timedelta(days=2): "Paixão de Cristo",
                       easter + timedelta(days=60): "Corpus Christi"}
            while day.year == year and day <= end:
                holiday = movable.get(day) or holidays.get((day.month, day.day))
                rows.append((day.isoformat(), int(day.weekday() != 6 and not holiday), holiday))
                day += timedelta(days=1)
        with db:
            db.executemany("INSERT OR IGNORE INTO dim_calendario_campinas VALUES (?,?,?)", rows)


def dashboard(db, query):
    first, latest = db.execute(
        "SELECT MIN(data_emissao), MAX(data_emissao) FROM fato_item_orcamento"
    ).fetchone()
    latest = date.fromisoformat(latest)
    available_years = list(range(int(first[:4]), latest.year + 1))
    raw_years = query.get("ano", [latest.year])
    years = available_years if raw_years == ["todos"] else sorted({int(value) for value in raw_years})
    if any(year not in available_years for year in years):
        raise ValueError("Ano fora da carga.")

    def selected(key, size):
        allowed = set(range(1, size + 1))
        chosen = {int(value) for value in query.get(key, [])}
        if chosen - allowed:
            raise ValueError("Período inválido.")
        return chosen or allowed

    months = selected("mes", 12)
    quarters = selected("trimestre", 4)
    semesters = selected("semestre", 2)
    months = sorted(m for m in months if (m - 1) // 3 + 1 in quarters and (m - 1) // 6 + 1 in semesters)
    spans = []
    for year in years:
        eligible = [m for m in months if year < latest.year or m <= latest.month]
        if not eligible:
            continue
        month = eligible[-1]
        cutoff = min(date(year, month, calendar.monthrange(year, month)[1]), latest)
        prior = date(year - 1, month, min(cutoff.day, calendar.monthrange(year - 1, month)[1]))
        spans.append((cutoff, prior))
    filters = [(column, query[key][0]) for key, column in DIMENSIONS.items() if query.get(key)]
    predicate = " WHERE f.data_emissao BETWEEN ? AND ?" + "".join(
        f" AND {column}=?" for column, _ in filters
    )
    values = [value for _, value in filters]

    def metrics(start, end):
        return db.execute(
            f"SELECT {MEASURES} {JOINS} {predicate} AND CAST(substr(f.data_emissao,6,2) AS INTEGER) "
            f"IN ({','.join('?' for _ in months)})",
            [start, end, *values, *months],
        ).fetchone()

    def grouped(column, start, end):
        return db.execute(
            f"SELECT COALESCE({column}, 'Sem informação'), {MEASURES} "
            f"{JOINS} {predicate} AND CAST(substr(f.data_emissao,6,2) AS INTEGER) "
            f"IN ({','.join('?' for _ in months)}) GROUP BY 1",
            [start, end, *values, *months],
        ).fetchall()

    def totals(previous):
        parts = [metrics(date(end.year, 1, 1), end) for end in (pair[previous] for pair in spans)]
        return [sum(part[i] for part in parts) for i in range(2)]

    def ranking(column):
        result = {}
        for end, _ in spans:
            for label, count, cents in grouped(column, date(end.year, 1, 1), end):
                old_count, old_cents = result.get(label, (0, 0))
                result[label] = (old_count + count, old_cents + cents)
        return [[label, *metrics] for label, metrics in result.items()]

    rankings = {
        key: query.get(key, [default])[0]
        for key, default in (("receita", "vendedor"), ("quantidade", "unidade"), ("preco", "servico"))
    }
    if any(value not in ("servico", "grupo", "vendedor", "unidade", "segmento") for value in rankings.values()):
        raise ValueError("Dimensão de ranking inválida.")
    grain = query.get("evolucao", ["mes"])[0]
    periods = {
        "mes": "substr(f.data_emissao,6,2)",
        "trimestre": "'T' || ((CAST(substr(f.data_emissao,6,2) AS INTEGER)-1)/3+1)",
        "semestre": "'S' || ((CAST(substr(f.data_emissao,6,2) AS INTEGER)-1)/6+1)",
        "ano": "CASE WHEN f.data_emissao IS NOT NULL THEN 'Ano' END",
    }
    if grain not in periods:
        raise ValueError("Agrupamento inválido.")
    options = {key: [row[0] for row in db.execute(
        f"SELECT DISTINCT {column} {JOINS} WHERE {column} IS NOT NULL AND {column} <> '' ORDER BY 1"
    )] for key, column in DIMENSIONS.items()}
    evolution = []
    for current, prior in spans:
        for series, end in enumerate((current, prior)):
            for label, count, cents in grouped(periods[grain], date(end.year, 1, 1), end):
                evolution.append([current.year, label, series, count, cents])
    target = None
    active_dimensions = [key for key in DIMENSIONS if query.get(key)]
    if not active_dimensions or active_dimensions in (["unidade"], ["vendedor"]):
        target = {"quantidade": None, "receita": None, "preco": None}
        target_months = [f"{end.year:04}-{month:02}" for end, _ in spans for month in months
                         if month <= end.month]
        if target_months and db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta_mensal'"
        ).fetchone():
            level = active_dimensions[0] if active_dimensions else "unidade"
            name = query[level][0] if active_dimensions else None
            condition = " AND nome=?" if name else ""
            count, quantity, revenue = db.execute(
                "SELECT COUNT(DISTINCT ano_mes), SUM(quantidade), SUM(quantidade*preco_medio_centavos) "
                f"FROM meta_mensal WHERE nivel=? AND ano_mes IN ({','.join('?' for _ in target_months)}){condition}",
                [level, *target_months, *([name] if name else [])],
            ).fetchone()
            if count == len(target_months):
                target = {"quantidade": quantity, "receita": revenue,
                          "preco": revenue / quantity if quantity else None}
    return {
        "anos": available_years, "opcoes": options,
        "meta": target,
        "atual": totals(0), "anterior": totals(1),
        "recorte": any(query.get(key) for key in ("mes", "trimestre", "semestre")),
        "cortes": [[current.isoformat(), prior.isoformat()] for current, prior in spans],
        "evolucao": evolution,
        "rankings": {key: ranking(DIMENSIONS[value]) for key, value in rankings.items()},
        "clientes": ranking(DIMENSIONS["cliente"]),
    }


def portfolio_summary(activity, historical_days, current_days, full_days):
    if not historical_days or current_days < 5:
        return None
    previous_qty, previous_value, current_qty, current_value = (
        sum(values[index] for values in activity.values()) for index in range(4)
    )
    if not previous_qty:
        return None
    previous_price = previous_value / previous_qty
    current_price = current_value / current_qty if current_qty else None
    projection = full_days / current_days
    baseline = full_days / historical_days
    volume = []
    prices = []
    revenue = []
    for name, (old_qty, old_value, qty, value) in activity.items():
        expected = old_qty * baseline
        projected = qty * projection
        volume.append([name, projected - expected, projected, expected])
        expected_value = old_value * baseline
        projected_value = value * projection
        revenue.append([name, projected_value - expected_value, projected_value, expected_value])
        if current_qty:
            # Contribuição aditiva, centrada no PM anterior. Inclui variações de preço e mix.
            impact = (value - previous_price * qty) / current_qty
            impact -= (old_value - previous_price * old_qty) / previous_qty
            prices.append([name, impact, value / qty if qty else None,
                           old_value / old_qty if old_qty else None])

    def highlights(rows, minimum):
        return {
            "altas": sorted((row for row in rows if row[1] >= minimum), key=lambda row: -row[1])[:3],
            "quedas": sorted((row for row in rows if row[1] <= -minimum), key=lambda row: row[1])[:3],
        }

    return {
        "quantidade": current_qty * projection, "esperado": previous_qty * baseline,
        "receita": current_value * projection, "receitaEsperada": previous_value * baseline,
        "preco": current_price, "precoAnterior": previous_price,
        "volume": highlights(volume, 0.5), "precos": highlights(prices, 0.5),
        "receitas": highlights(revenue, 1),
    }


def customer_report(db, query):
    first, last = db.execute("SELECT MIN(data_emissao), MAX(data_emissao) FROM fato_item_orcamento").fetchone()
    end = date.fromisoformat(query.get("fim", [last])[0])
    if not first <= end.isoformat() <= last:
        raise ValueError("Data fora da carga.")
    start = end.replace(day=1)
    previous = []
    month = start
    for _ in range(3):
        month = (month - timedelta(days=1)).replace(day=1)
        previous.insert(0, month.isoformat()[:7])

    dimensions = {key: DIMENSIONS[key] for key in ("vendedor", "unidade")}
    options = {key: [row[0] for row in db.execute(f"SELECT DISTINCT {column} {JOINS} ORDER BY 1")]
               for key, column in dimensions.items()}
    selected = {key: query.get(key, [""])[0] for key in dimensions}
    if all(selected.values()):
        raise ValueError("Selecione unidade ou vendedor.")
    filters = "".join(f" AND {column}=?" for key, column in dimensions.items() if selected[key])
    params = [month.isoformat(), end.isoformat(), *(value for value in selected.values() if value)]
    rows = db.execute(
        f"SELECT {DIMENSIONS['cliente']}, CASE WHEN f.data_emissao >= ? THEN f.data_emissao "
        f"ELSE substr(f.data_emissao,1,7) END, {MEASURES} {JOINS} "
        f"WHERE f.data_emissao BETWEEN ? AND ?{filters} GROUP BY 1,2",
        [start.isoformat(), *params],
    ).fetchall()
    sales_days = {period for _, period, _, _ in rows if len(period) == 10}
    days = [day for day, useful in db.execute(
        "SELECT data, dia_util FROM dim_calendario_campinas WHERE data BETWEEN ? AND ? ORDER BY data",
        (start.isoformat(), end.isoformat()),
    ) if useful or day in sales_days]
    columns = {period: index for index, period in enumerate(previous + days)}
    matrix = {}
    revenue = dict.fromkeys(previous + [start.isoformat()[:7]], 0)
    activity = {}
    for client, period, quantity, cents in rows:
        counts = matrix.setdefault(client, [0] * len(columns))
        counts[columns[period]] = quantity
        revenue[period[:7]] += cents
        values = activity.setdefault(client, [0, 0, 0, 0])
        offset = 2 if len(period) == 10 else 0
        values[offset] += quantity
        values[offset + 1] += cents
    clients = [[client, *counts, sum(counts[3:])] for client, counts in matrix.items()]
    clients.sort(key=lambda row: (-row[-1], -sum(row[1:4]), row[0].casefold()))
    totals = [sum(row[i] for row in clients) for i in range(1, len(columns) + 2)]
    useful_days = dict(db.execute(
        "SELECT substr(data,1,7), SUM(dia_util) FROM dim_calendario_campinas "
        "WHERE data BETWEEN ? AND ? GROUP BY 1",
        (month.isoformat(), end.isoformat()),
    ))
    monthly_summary = [
        [quantity / useful_days[period] if useful_days.get(period) else None,
         revenue[period] / quantity if quantity else None]
        for period, quantity in zip(revenue, [*totals[:3], totals[-1]])
    ]
    periods = previous + [start.isoformat()[:7]]
    days_by_month = [useful_days.get(period, 0) for period in periods]
    full_days = db.execute("SELECT dias_uteis FROM dias_uteis_por_mes WHERE ano_mes=?", (periods[-1],)).fetchone()[0]
    elapsed_days = days_by_month[-1]
    projection = full_days / elapsed_days if elapsed_days else None
    client_speed = [row[-1] * projection if projection is not None else None for row in clients]
    trends = []
    for _, *counts in clients:
        historical_days = sum(days_by_month[:3])
        baseline = sum(counts[:3]) / historical_days if historical_days else 0
        if baseline == 0 or days_by_month[-1] < 5:
            trends.append(["Sem base", None])
            continue
        change = (counts[-1] / days_by_month[-1] / baseline - 1) * 100
        trend = "Crescimento" if change >= 15 else "Declínio" if change <= -15 else "Estabilidade"
        trends.append([trend, round(change, 1)])
    return {"primeira": first, "ultima": last, "fim": end.isoformat(),
            "opcoes": options, "filtros": selected,
            "meses": previous, "dias": days, "clientes": clients, "totais": totals,
            "resumoMensal": monthly_summary, "tendencias": trends, "speedClientes": client_speed,
            "speedTotal": totals[-1] * projection if projection is not None else None,
            "executivo": portfolio_summary(activity, sum(days_by_month[:3]), elapsed_days, full_days)}


def management_report(db, query, inspectors=False, cutoff=None):
    first, last = db.execute("SELECT MIN(data_emissao), MAX(data_emissao) FROM fato_item_orcamento").fetchone()
    available_years = list(range(int(first[:4]), int(last[:4]) + 1))
    raw_years = query.get("ano", [available_years[-1]])
    years = available_years if raw_years == ["todos"] else sorted({int(value) for value in raw_years})
    months = sorted({int(value) for value in query.get("mes", range(1, 13))})
    if any(year not in available_years for year in years) or any(not 1 <= month <= 12 for month in months):
        raise ValueError("Recorte inválido.")
    dimensions = {key: DIMENSIONS[key] for key in ("unidade", "vendedor", "grupo", "segmento", "cliente")}
    options = {key: [row[0] for row in db.execute(f"SELECT DISTINCT {column} {JOINS} ORDER BY 1")]
               for key, column in dimensions.items()}
    selected = {key: query[key][0] for key in dimensions if query.get(key)}
    if any(value not in options[key] for key, value in selected.items()):
        raise ValueError("Seleção inválida.")
    periods = [f"{year:04}-{month:02}" for year in years for month in months
               if first[:7] <= f"{year:04}-{month:02}" <= last[:7]]
    if not periods:
        return {"anos": available_years, "opcoes": options, "periodos": [],
                **({"vistoriadores": []} if inspectors else {"graficos": [[] for _ in range(9)]})}
    first_day = periods[0] + "-01"
    final_month = date.fromisoformat(periods[-1] + "-01")
    final_day = min(last, final_month.replace(day=calendar.monthrange(final_month.year, final_month.month)[1]).isoformat())
    if cutoff:
        final_day = min(final_day, cutoff)
    condition = "".join(f" AND {dimensions[key]}=?" for key in selected)
    args = (first_day, final_day, *selected.values())
    if inspectors:
        rows = db.execute(
            f"SELECT COALESCE(NULLIF(TRIM(i.nome), ''), 'Sem informação'), "
            f"{DIMENSIONS['grupo']}, f.vistoria_movel, {MEASURES} {JOINS} "
            "LEFT JOIN dim_vistoriador i ON i.id_erp=f.id_vistoriador "
            f"WHERE f.data_emissao BETWEEN ? AND ?{condition} "
            f"AND substr(f.data_emissao,1,7) IN ({','.join('?' for _ in periods)}) GROUP BY 1,2,3",
            (*args, *periods),
        )
        totals = {}
        for name, group, mobile, count, cents in rows:
            values = totals.setdefault(name, [0] * 8)
            values[("Transferência", "Cautelar", "Outros").index(group)] += count
            values[3 if mobile == 0 else 4 if mobile == 1 else 5] += count
            values[6] += count
            values[7] += cents
        return {"anos": available_years, "opcoes": options, "periodos": periods,
                "vistoriadores": [[name, *values] for name, values in sorted(
                    totals.items(), key=lambda item: (-item[1][6], item[0].casefold())
                )]}
    rows = db.execute(
        f"SELECT substr(f.data_emissao,1,7), {DIMENSIONS['grupo']}, {MEASURES} {JOINS} "
        f"WHERE f.data_emissao BETWEEN ? AND ?{condition} GROUP BY 1,2", args,
    )
    groups = {(period, group): (count, value) for period, group, count, value in rows}
    days = dict(db.execute(
        "SELECT substr(data,1,7), SUM(dia_util) FROM dim_calendario_campinas "
        "WHERE data BETWEEN ? AND ? GROUP BY 1", args[:2],
    ))
    charts = [[] for _ in range(9)]
    for period in periods:
        transfer, cautelar, other = (groups.get((period, group), (0, 0))
                                     for group in ("Transferência", "Cautelar", "Outros"))
        count = transfer[0] + cautelar[0] + other[0]
        revenue = transfer[1] + cautelar[1] + other[1]
        useful_days = days.get(period)
        metrics = (revenue, revenue / count if count else None,
                   revenue / useful_days if useful_days else None,
                   transfer[1], transfer[1] / transfer[0] if transfer[0] else None,
                   cautelar[1], cautelar[1] / cautelar[0] if cautelar[0] else None,
                   transfer[1] / useful_days if useful_days else None,
                   cautelar[1] / useful_days if useful_days else None)
        for series, value in zip(charts, metrics):
            series.append(value)
    return {"anos": available_years, "opcoes": options, "periodos": periods, "graficos": charts}


def speed_report(db, query):
    report = customer_report(db, query)
    end = report["fim"]
    start = end[:7] + "-01"
    days, full_days = db.execute(
        "SELECT SUM(CASE WHEN data<=? THEN dia_util ELSE 0 END), SUM(dia_util) "
        "FROM dim_calendario_campinas WHERE substr(data,1,7)=?", (end, end[:7])
    ).fetchone()
    selected = {key: value for key, value in report["filtros"].items() if value}
    condition = "".join(f" AND {DIMENSIONS[key]}=?" for key in selected)
    groups = {group: (count, cents) for group, count, cents in db.execute(
        f"SELECT {DIMENSIONS['grupo']}, {MEASURES} {JOINS} "
        f"WHERE f.data_emissao BETWEEN ? AND ?{condition} GROUP BY 1",
        (start, end, *selected.values()),
    )}
    values = [groups.get(group, (0, 0)) for group in ("Transferência", "Cautelar", "Outros")]
    count = sum(item[0] for item in values)
    revenue = sum(item[1] for item in values)
    values.append((count, revenue))
    ratio = full_days / days if days else None
    projected = [values[index] for index in (0, 1, 3)]
    report["speed"] = {
        "inicio": start, "dias": days, "diasMes": full_days,
        "linhas": [
            [*(item[0] for item in values), *(item[0] * ratio if ratio is not None else None for item in projected)],
            [*(item[1] / item[0] if item[0] else None for item in values),
             *(item[1] / item[0] if item[0] else None for item in projected)],
            [*(item[1] for item in values), *(item[1] * ratio if ratio is not None else None for item in projected)],
        ],
    }
    filters = {key: [value] for key, value in selected.items()}
    filters.update(ano=[end[:4]], mes=[end[5:7]])
    report["vistoriadores"] = management_report(db, filters, inspectors=True, cutoff=end)["vistoriadores"]
    return report


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        database = DATABASE
        if url.path in ("/api/visao", "/api/relatorio-cliente", "/api/gerencial", "/api/vistoriadores", "/api/speed"):
            maintain_calendar(database)
        if url.path in ("/api/gerencial", "/api/vistoriadores"):
            try:
                with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
                    body = management_report(db, parse_qs(url.query), inspectors=url.path == "/api/vistoriadores")
                self.reply(json.dumps(body, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            except ValueError:
                self.send_error(400, "Recorte invalido")
            return
        if url.path in ("/api/relatorio-cliente", "/api/speed"):
            query = parse_qs(url.query)
            try:
                with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
                    body = (speed_report if url.path == "/api/speed" else customer_report)(db, query)
                self.reply(json.dumps(body, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            except ValueError:
                self.send_error(400, "Periodo invalido")
            return
        if url.path == "/api/visao":
            try:
                with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
                    body = dashboard(db, parse_qs(url.query))
                self.reply(json.dumps(body, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            except ValueError:
                self.send_error(400, "Filtros invalidos")
            return
        files = {
            "/": ("painel.html", "text/html; charset=utf-8"),
            "/relatorio-cliente": ("relatorio_cliente.html", "text/html; charset=utf-8"),
            "/speed": ("relatorio_cliente.html", "text/html; charset=utf-8"),
            "/graficos_vistoriadores.js": ("graficos_vistoriadores.js", "text/javascript; charset=utf-8"),
            "/gerencial": ("gerencial.html", "text/html; charset=utf-8"),
            "/vistoriadores": ("gerencial.html", "text/html; charset=utf-8"),
            "/logo-auto-visao.png": ("Logo_AutoVisao_PNG.png", "image/png"),
            "/og-banner.png": ("og-banner.png", "image/png"),
            "/filtros.js": ("filtros.js", "text/javascript; charset=utf-8"),
            "/design-system.css": ("design-system.css", "text/css; charset=utf-8"),
            "/assets/fonts/fonts.css": ("assets/fonts/fonts.css", "text/css; charset=utf-8"),
            **{f"/assets/fonts/auto-visao-font-{i}.ttf":
               (f"assets/fonts/auto-visao-font-{i}.ttf", "font/ttf") for i in range(1, 7)},
        }
        if url.path not in files:
            self.send_error(404)
            return
        filename, mime = files[url.path]
        self.reply((ROOT / filename).read_bytes(), mime)

    def reply(self, body, mime):
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    if not DATABASE.is_file():
        raise SystemExit("Base SQLite ausente. Confira BI_DATABASE.")
    print(f"BI de Vendas em http://{HOST}:{PORT}/", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

