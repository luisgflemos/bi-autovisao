from collections import defaultdict
from html import escape
from datetime import date


def validate_target_database(db):
    rows = db.execute("""
        SELECT ano_mes, id_unidade, id_servico, id_vistoriador, quantidade, faturamento_centavos
        FROM meta_produto_vistoriador
    """)
    seen = set()
    for month, unit, service, inspector, quantity, revenue in rows:
        if not isinstance(month, str) or date.fromisoformat(month + "-01").isoformat()[:7] != month:
            raise ValueError("mês inválido")
        if any(not isinstance(value, str) or not value.strip() for value in (unit, service, inspector)):
            raise ValueError("identificador inválido")
        if type(quantity) is not int or quantity <= 0 or type(revenue) is not int or revenue < 0:
            raise ValueError("quantidade ou faturamento inválido")
        key = (month, unit, service, inspector)
        if key in seen:
            raise ValueError("meta duplicada")
        seen.add(key)
    if not seen:
        raise ValueError("arquivo de metas vazio")


def format_number(value, money=False):
    if value is None:
        return "—"
    if money:
        return "R$ " + f"{value / 100:,.2f}".translate(str.maketrans(",.", ".,"))
    return f"{value:,}".replace(",", ".")


def comparison_page(db, query):
    header = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Realizado × Meta</title><link rel="stylesheet" href="/design-system.css">
    <style>
    main{padding:24px}h1{margin:12px 0}form{display:flex;flex-wrap:wrap;align-items:end;gap:12px;margin:20px 0}
    label{display:grid;gap:4px}select{max-width:min(340px,85vw)}.comparison{overflow:auto}
    table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
    th,td{padding:10px;border:1px solid #ddd;text-align:right;white-space:nowrap}
    td:first-child,th:first-child{text-align:left}thead{background:#252525;color:white}
    tbody tr:nth-child(even){background:#f4f4f4}tbody tr:first-child{font-weight:bold;background:#fbe8e8}
    </style></head><body><main><nav class="view-nav" aria-label="Visões do BI">
    <a href="/">Visão Geral</a><a href="/realizado-meta" aria-current="page">Realizado × Meta</a>
    </nav><h1>Realizado × Meta</h1>"""
    footer = "</main></body></html>"
    exists = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta_produto_vistoriador' "
        "UNION ALL SELECT 1 FROM sqlite_temp_master WHERE name='meta_produto_vistoriador'"
    ).fetchone()
    if not exists:
        return header + "<p>Sem metas detalhadas nesta base.</p>" + footer

    unit = query.get("unidade", [""])[0]
    units = db.execute("""SELECT DISTINCT u.id_erp, u.nome FROM meta_produto_vistoriador m
                          JOIN dim_unidade u ON u.id_erp=m.id_unidade ORDER BY u.nome""").fetchall()
    if not units:
        return header + "<p>Sem metas detalhadas nesta base.</p>" + footer
    unit = unit if unit in dict(units) else units[0][0]
    last_date = db.execute("SELECT MAX(data_emissao) FROM fato_item_orcamento").fetchone()[0] or ""
    months = [row[0] for row in db.execute(
        "SELECT DISTINCT ano_mes FROM meta_produto_vistoriador WHERE id_unidade=? ORDER BY ano_mes", (unit,)
    )]
    default_month = next((m for m in reversed(months) if m <= last_date[:7]), months[0])
    month = query.get("mes", [default_month])[0]
    month = month if month in months else default_month
    targets = db.execute("""SELECT s.nome, i.nome, m.quantidade, m.faturamento_centavos
        FROM meta_produto_vistoriador m JOIN dim_servico s ON s.id_erp=m.id_servico
        JOIN dim_vistoriador i ON i.id_erp=m.id_vistoriador WHERE m.id_unidade=? AND m.ano_mes=?""",
        (unit, month)).fetchall()
    actuals = db.execute("""SELECT s.nome, COALESCE(NULLIF(TRIM(i.nome),''),'Sem informação'),
        COUNT(*), SUM(f.valor_liquido_centavos)
        FROM fato_item_orcamento f JOIN dim_servico s ON s.id_erp=f.id_servico
        LEFT JOIN dim_vistoriador i ON i.id_erp=f.id_vistoriador
        WHERE f.id_unidade=? AND f.data_emissao>=? AND f.data_emissao<=? GROUP BY 1, 2""",
        (unit, month + "-01", month + "-31")).fetchall()
    products = sorted({row[0] for row in targets + actuals}, key=str.casefold)
    inspectors = sorted({row[1] for row in targets + actuals}, key=str.casefold)
    product = query.get("produto", [""])[0]
    inspector = query.get("vistoriador", [""])[0]
    grouping = query.get("agrupar", ["produto"])[0]
    grouping = grouping if grouping in ("produto", "vistoriador") else "produto"

    def select(key, label, options, chosen):
        tags = []
        for value, name in options:
            selected = " selected" if value == chosen else ""
            tags.append(f'<option value="{escape(value)}"{selected}>{escape(name)}</option>')
        return f'<label>{label}<select name="{key}">{"".join(tags)}</select></label>'

    filters = "".join((
        select("unidade", "Unidade", units, unit),
        select("mes", "Mês", [(m, m[5:7] + "/" + m[:4]) for m in months], month),
        select("produto", "Produto", [("", "Todos"), *((p, p) for p in products)], product),
        select("vistoriador", "Vistoriador", [("", "Todos"), *((i, i) for i in inspectors)], inspector),
        select("agrupar", "Agrupar por", [("produto", "Produto"), ("vistoriador", "Vistoriador")], grouping),
    ))

    def aggregate(rows):
        result = defaultdict(lambda: [0, 0])
        for service, person, qty, cents in rows:
            if (product and service != product) or (inspector and person != inspector):
                continue
            key = service if grouping == "produto" else person
            result[key][0] += qty
            result[key][1] += cents
        return result

    target = aggregate(targets)
    actual = aggregate(actuals)
    has_actual = month <= last_date[:7]

    def cells(name, actual_values, target_values):
        aq, ar = actual_values
        tq, tr = target_values
        ap = ar / aq if aq else None
        tp = tr / tq if tq else None
        values = [aq, tq, ar, tr, ap, tp]
        formatted = [format_number(value, index >= 2) for index, value in enumerate(values)]
        return "<tr><td>" + escape(name) + "</td>" + "".join(f"<td>{v}</td>" for v in formatted) + "</tr>"

    def total(rows):
        return tuple(map(sum, zip(*rows.values()))) if rows else (None, None)

    empty_actual = (0, 0) if has_actual else (None, None)
    rows = cells("Total do recorte", total(actual) if actual else empty_actual, total(target))
    for key in sorted(actual.keys() | target.keys(), key=str.casefold):
        rows += cells(key, actual.get(key, empty_actual), target.get(key, (None, None)))
    label = "Produto" if grouping == "produto" else "Vistoriador"
    status = f"Último realizado disponível em {last_date[8:10]}/{last_date[5:7]}/{last_date[:4]}." if last_date else ""
    return header + f"""<form>{filters}<button>Aplicar filtros</button></form>
    <p>{status} Meta rateada pela equipe estimada da unidade.</p>
    <div class="comparison"><table><thead>
    <tr><th rowspan="2">{label}</th><th colspan="2">Quantidade</th>
    <th colspan="2">Faturamento</th><th colspan="2">Preço Médio</th></tr>
    <tr><th>Realizado</th><th>Meta</th><th>Realizado</th><th>Meta</th><th>Realizado</th><th>Meta</th></tr>
    </thead><tbody>{rows}</tbody></table></div>""" + footer
