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
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta_cliente'").fetchone():
        invalid = db.execute("""
            SELECT 1 FROM meta_cliente
            WHERE typeof(quantidade_milionesimos)<>'integer' OR quantidade_milionesimos<=0
               OR typeof(faturamento_centavos)<>'integer' OR faturamento_centavos<0
            LIMIT 1
        """).fetchone()
        if invalid:
            raise ValueError("rateio por cliente inválido")
        duplicates = db.execute("""
            SELECT 1 FROM meta_cliente
            GROUP BY ano_mes,id_unidade,id_servico,id_vistoriador,id_cliente
            HAVING COUNT(*)>1
            UNION ALL
            SELECT 1 FROM meta_cliente_vendedor GROUP BY id_cliente HAVING COUNT(*)>1
            LIMIT 1
        """).fetchone()
        if duplicates:
            raise ValueError("cliente duplicado na distribuição ou no vínculo de vendedor")
        mismatch = db.execute("""
            WITH original AS (
                SELECT ano_mes,id_unidade,id_servico,id_vistoriador,
                       quantidade*1000000 q,faturamento_centavos r
                FROM meta_produto_vistoriador
            ), detail AS (
                SELECT ano_mes,id_unidade,id_servico,id_vistoriador,
                       SUM(quantidade_milionesimos) q,SUM(faturamento_centavos) r
                FROM meta_cliente GROUP BY 1,2,3,4
            )
            SELECT * FROM (SELECT * FROM original EXCEPT SELECT * FROM detail)
            UNION ALL
            SELECT * FROM (SELECT * FROM detail EXCEPT SELECT * FROM original)
            LIMIT 1
        """).fetchone()
        if mismatch:
            raise ValueError("rateio por cliente não reconcilia com a meta original")
        inconsistent = db.execute("""
            SELECT 1 FROM meta_cliente m LEFT JOIN meta_cliente_vendedor v
            ON v.id_cliente=m.id_cliente
            WHERE v.id_cliente IS NULL OR m.id_vendedor IS NOT v.id_vendedor
            LIMIT 1
        """).fetchone()
        if inconsistent:
            raise ValueError("vendedor do cliente inconsistente")


def format_number(value, money=False):
    if value is None:
        return "—"
    if money:
        return "R$ " + f"{value / 100:,.2f}".translate(str.maketrans(",.", ".,"))
    return f"{value:,.3f}".rstrip("0").rstrip(".").translate(str.maketrans(",.", ".,"))


def comparison_page(db, query, can_import=False):
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
    if can_import:
        header += """<details><summary>Importar metas</summary>
        <p>Envie um arquivo SQLite com o conjunto completo de metas. A importação substitui as metas cadastradas.</p>
        <form id="importar-metas">
          <label>Arquivo de metas <input type="file" accept=".sqlite3,.sqlite,.db" required></label>
          <button>Importar metas</button>
        </form>
        <p id="resultado-importacao" role="status"></p>
        <script>
        document.querySelector('#importar-metas').addEventListener('submit', async (event) => {
          event.preventDefault();
          const form = event.currentTarget;
          const button = form.querySelector('button');
          const status = document.querySelector('#resultado-importacao');
          button.disabled = true;
          status.textContent = 'Validando e importando…';
          try {
            const response = await fetch('/api/importar-metas', {
              method: 'PUT',
              headers: {'Content-Type': 'application/octet-stream'},
              body: form.querySelector('input').files[0]
            });
            if (response.ok) {
              status.textContent = 'Metas importadas.';
              window.location.reload();
            } else if (response.status === 400) {
              status.textContent = await response.text();
            } else {
              status.textContent = 'Importação recusada. Confira sua sessão e o tamanho do arquivo (até 32 MB).';
            }
          } catch {
            status.textContent = 'Falha na conexão. Confira o comparativo antes de tentar novamente.';
          } finally {
            button.disabled = false;
          }
        });
        </script></details>"""
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
    client_detail = db.execute(
        "SELECT 1 FROM metas.sqlite_master WHERE type='table' AND name='meta_cliente'"
    ).fetchone() if any(row[1] == "metas" for row in db.execute("PRAGMA database_list")) else None
    if client_detail:
        targets = db.execute("""
            SELECT s.nome,i.nome,m.quantidade_milionesimos/1000000.0,m.faturamento_centavos,
                   COALESCE(NULLIF(TRIM(c.nome_fantasia),''),'Sem informação'),
                   COALESCE(NULLIF(TRIM(v.nome),''),'Corporativo'),
                   COALESCE(NULLIF(TRIM(c.segmento),''),'Sem informação')
            FROM metas.meta_cliente m
            JOIN dim_servico s ON s.id_erp=m.id_servico
            JOIN dim_vistoriador i ON i.id_erp=m.id_vistoriador
            LEFT JOIN dim_cliente c ON c.id_erp=NULLIF(m.id_cliente,'')
            LEFT JOIN dim_vendedor v ON v.id_erp=m.id_vendedor
            WHERE m.id_unidade=? AND m.ano_mes=?
        """, (unit, month)).fetchall()
        actuals = db.execute("""
            SELECT s.nome,COALESCE(NULLIF(TRIM(i.nome),''),'Sem informação'),
                   COUNT(*),SUM(f.valor_liquido_centavos),
                   COALESCE(NULLIF(TRIM(c.nome_fantasia),''),'Sem informação'),
                   COALESCE(NULLIF(TRIM(v.nome),''),'Corporativo'),
                   COALESCE(NULLIF(TRIM(c.segmento),''),'Sem informação')
            FROM fato_item_orcamento f JOIN dim_servico s ON s.id_erp=f.id_servico
            LEFT JOIN dim_vistoriador i ON i.id_erp=f.id_vistoriador
            LEFT JOIN dim_cliente c ON c.id_erp=f.id_cliente
            LEFT JOIN metas.meta_cliente_vendedor carteira ON carteira.id_cliente=COALESCE(f.id_cliente,'')
            LEFT JOIN dim_vendedor v ON v.id_erp=CASE WHEN carteira.id_cliente IS NOT NULL
                THEN carteira.id_vendedor ELSE f.id_vendedor END
            WHERE f.id_unidade=? AND f.data_emissao>=? AND f.data_emissao<=?
            GROUP BY 1,2,5,6,7
        """, (unit, month + "-01", month + "-31")).fetchall()
    products = sorted({row[0] for row in targets + actuals}, key=str.casefold)
    inspectors = sorted({row[1] for row in targets + actuals}, key=str.casefold)
    product = query.get("produto", [""])[0]
    inspector = query.get("vistoriador", [""])[0]
    grouping = query.get("agrupar", ["produto"])[0]
    groupings = [("produto", "Produto"), ("vistoriador", "Vistoriador")]
    if client_detail:
        groupings.extend([("cliente", "Cliente"), ("vendedor", "Vendedor"), ("segmento", "Segmento")])
    grouping = grouping if grouping in dict(groupings) else "produto"
    client_filters = {}
    for position, key in ((4, "cliente"), (5, "vendedor"), (6, "segmento")):
        if client_detail:
            client_filters[key] = (position, query.get(key, [""])[0])

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
        *(select(key, dict(groupings)[key], [("", "Todos"), *((value, value) for value in sorted(
            {row[position] for row in targets + actuals}
        ))], chosen) for key, (position, chosen) in client_filters.items()),
        select("agrupar", "Agrupar por", groupings, grouping),
    ))

    def aggregate(rows):
        result = defaultdict(lambda: [0, 0])
        for row in rows:
            service, person, qty, cents = row[:4]
            if (product and service != product) or (inspector and person != inspector):
                continue
            if any(chosen and row[position] != chosen for position, chosen in client_filters.values()):
                continue
            position = {"produto": 0, "vistoriador": 1, "cliente": 4, "vendedor": 5, "segmento": 6}[grouping]
            key = row[position]
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
    label = dict(groupings)[grouping]
    status = f"Último realizado disponível em {last_date[8:10]}/{last_date[5:7]}/{last_date[:4]}." if last_date else ""
    allocation_note = ("Meta proporcional ao histórico por produto. Realizado e meta por vendedor "
                       "usam o último vínculo observado do cliente; quantidades rateadas podem ser fracionadas."
                       if client_detail else "Meta rateada pela equipe estimada da unidade.")
    return header + f"""<form>{filters}<button>Aplicar filtros</button></form>
    <p>{status} {allocation_note}</p>
    <div class="comparison"><table><thead>
    <tr><th rowspan="2">{label}</th><th colspan="2">Quantidade</th>
    <th colspan="2">Faturamento</th><th colspan="2">Preço Médio</th></tr>
    <tr><th>Realizado</th><th>Meta</th><th>Realizado</th><th>Meta</th><th>Realizado</th><th>Meta</th></tr>
    </thead><tbody>{rows}</tbody></table></div>""" + footer
