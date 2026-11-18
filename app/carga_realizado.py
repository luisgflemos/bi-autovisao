"""Conciliação transacional de itens exportados do ERP, por unidade e pedido."""
import hashlib
import json
import sqlite3
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timezone


def normalized(value):
    return ' '.join(unicodedata.normalize('NFC', str(value or '')).split()).casefold()


def import_actuals(path, payload):
    start = date.fromisoformat(payload['inicio'])
    end = date.fromisoformat(payload['fim'])
    if start > end:
        raise ValueError('período inválido')
    rows = payload['linhas']
    if not isinstance(rows, list) or not rows or len(rows) > 100000:
        raise ValueError('quantidade de linhas inválida')
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    for row in rows:
        day = date.fromisoformat(row['data_emissao'])
        if not start <= day <= end or not row['pedido'] or not row['servico']:
            raise ValueError('linha sem pedido, serviço ou data válida no período')
        if type(row['valor_liquido_centavos']) is not int or row['quantidade'] != 1:
            raise ValueError('valor ou quantidade inválidos')
        if any(row[key] not in (0, 1, None) for key in ('item_faturado', 'vistoria_movel')):
            raise ValueError('indicador inválido')
    with sqlite3.connect(path.resolve().as_uri() + '?mode=rw', uri=True, timeout=60) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        has_log = db.execute("SELECT 1 FROM sqlite_master WHERE name='carga_realizado' AND type='table'").fetchone()
        if has_log:
            previous = db.execute('SELECT resultado FROM carga_realizado WHERE hash=?', (digest,)).fetchone()
            if previous:
                return {**json.loads(previous[0]), 'reprocessamento': True}
        backup_folder = path.parent / 'cargas-backup'
        backup_folder.mkdir(exist_ok=True)
        backup_name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '-' + digest[:12] + '.sqlite3'
        with sqlite3.connect(backup_folder / backup_name) as backup:
            db.backup(backup)
        db.execute('BEGIN IMMEDIATE')
        db.execute('''CREATE INDEX IF NOT EXISTS idx_fato_unidade_numero
                      ON fato_item_orcamento(id_unidade,numero_orcamento)''')
        db.execute('''CREATE TABLE IF NOT EXISTS carga_realizado (
            hash TEXT PRIMARY KEY, criado_em TEXT NOT NULL, resultado TEXT NOT NULL
        )''')
        previous = db.execute('SELECT resultado FROM carga_realizado WHERE hash=?', (digest,)).fetchone()
        if previous:
            return {**json.loads(previous[0]), 'reprocessamento': True}
        db.execute('''CREATE TABLE IF NOT EXISTS carga_realizado_auditoria (
            carga_hash TEXT NOT NULL, id_item TEXT NOT NULL, operacao TEXT NOT NULL,
            antes TEXT, depois TEXT
        )''')
        dimensions = {}
        for table in ('unidade', 'servico', 'vendedor', 'cliente', 'vistoriador', 'condicao_pagamento'):
            label = 'nome_fantasia' if table == 'cliente' else 'descricao' if table == 'condicao_pagamento' else 'nome'
            mapping = defaultdict(list)
            for record in db.execute(f'SELECT id_erp,{label} FROM dim_{table}'):
                mapping[normalized(record[1])].append(record[0])
            dimensions[table] = (label, mapping)

        def dimension(table, value, attributes=None):
            if not value:
                return None
            label, mapping = dimensions[table]
            candidates = mapping.get(normalized(value), [])
            if len(candidates) > 1:
                condition = ''.join(f' AND {key} IS ?' for key in attributes or {})
                exact = db.execute(f'SELECT id_erp FROM dim_{table} WHERE {label}=?{condition}',
                                   [value, *(attributes or {}).values()]).fetchall()
                if len(exact) != 1:
                    raise ValueError(f'dimensão ambígua: {table}, {value}')
                return exact[0][0]
            if candidates:
                return candidates[0]
            if table == 'unidade':
                raise ValueError(f'unidade desconhecida: {value}')
            identifier = 'carga:' + hashlib.sha256((table + '|' + normalized(value)).encode()).hexdigest()
            db.execute(f'INSERT INTO dim_{table}(id_erp,{label}) VALUES (?,?)', (identifier, value))
            mapping[normalized(value)] = [identifier]
            return identifier

        groups = defaultdict(list)
        clients = {}
        for row in rows:
            unit = dimension('unidade', row['unidade'] or 'Andrade Neves')
            client = dimension('cliente', row['cliente'], {key: row[key] for key in
                               ('periodicidade', 'agrupamento', 'segmento', 'macro_regiao')})
            record = {
                'numero_orcamento': row['pedido'], 'data_emissao': row['data_emissao'],
                'id_unidade': unit, 'id_servico': dimension('servico', row['servico']),
                'id_vendedor': dimension('vendedor', row['vendedor'] or 'Corporativo'),
                'id_cliente': client, 'id_vistoriador': dimension('vistoriador', row['vistoriador']),
                'id_condicao_pagamento': dimension('condicao_pagamento', row['condicao_pagamento']),
                **{key: row[key] for key in ('tipo_veiculo', 'marca_veiculo', 'modelo_veiculo',
                    'vistoria_movel', 'item_faturado', 'quantidade', 'valor_liquido_centavos')},
                'desconto_centavos': None,
            }
            groups[unit, row['pedido'], not bool(row['unidade'])].append(record)
            if client and (client not in clients or row['data_emissao'] >= clients[client][0]):
                clients[client] = (row['data_emissao'], row)
        for client, (_, row) in clients.items():
            db.execute('''UPDATE dim_cliente SET periodicidade=?,agrupamento=?,segmento=?,macro_regiao=?
                          WHERE id_erp=?''', (row['periodicidade'], row['agrupamento'], row['segmento'], row['macro_regiao'], client))
        result = {'linhas_arquivo': len(rows), 'inseridos': 0, 'atualizados': 0,
                  'inalterados': 0, 'substituidos': 0, 'fora_do_arquivo_preservados': 0,
                  'datas_corrigidas_fora_janela': 0,
                  'inicio': start.isoformat(), 'fim': end.isoformat(),
                  'valor_arquivo_centavos': sum(row['valor_liquido_centavos'] for row in rows),
                  'backup': backup_name}
        seen = set()
        corporate = dimension('vendedor', 'Corporativo')
        for (unit, number, unidentified_unit), incoming in groups.items():
            existing = [dict(row) for row in db.execute(
                'SELECT * FROM fato_item_orcamento WHERE id_unidade=? AND numero_orcamento=? ORDER BY id_item_erp',
                (unit, number),
            )]
            if unidentified_unit:
                # O número sem a unidade de origem não identifica um pedido histórico da loja padrão.
                incoming_clients = {row['id_cliente'] for row in incoming}
                existing = [row for row in existing if row['id_item_erp'].startswith('carga:')
                            and row['id_cliente'] in incoming_clients]
            seen.update(row['id_item_erp'] for row in existing)
            order_ids = {row['id_orcamento_erp'] for row in existing}
            if len(order_ids) > 1:
                raise ValueError(f'pedido {number} tem mais de uma chave histórica')
            order_id = next(iter(order_ids), 'carga:' + hashlib.sha256((unit + '|' + number).encode()).hexdigest())
            unmatched = list(existing)
            changed = []
            for record in incoming:
                exact = next((old for old in unmatched if all(
                    old[key] == value or (key == 'id_vendedor' and old[key] is None and value == corporate)
                    for key, value in record.items()
                )), None)
                if exact:
                    unmatched.remove(exact)
                    result['inalterados'] += 1
                else:
                    changed.append(record)
            for record in changed:
                candidates = [old for old in unmatched if old['id_servico'] == record['id_servico']]
                old = candidates[0] if candidates else unmatched[0] if len(unmatched) == len(changed) == 1 else None
                db.execute('INSERT OR IGNORE INTO dim_data VALUES (?)', (record['data_emissao'],))
                if old:
                    unmatched.remove(old)
                    identifier = old['id_item_erp']
                    assignments = ','.join(f'{key}=?' for key in record)
                    db.execute(f'UPDATE fato_item_orcamento SET {assignments} WHERE id_item_erp=?',
                               [*record.values(), identifier])
                    operation = 'atualizado'
                    result['atualizados'] += 1
                    if not start.isoformat() <= old['data_emissao'] <= end.isoformat():
                        result['datas_corrigidas_fora_janela'] += 1
                else:
                    identifier = 'carga:' + hashlib.sha256((digest + '|' + unit + '|' + number + '|' + str(result['inseridos'])).encode()).hexdigest()
                    values = {'id_item_erp': identifier, 'id_orcamento_erp': order_id, **record}
                    db.execute(f"INSERT INTO fato_item_orcamento({','.join(values)}) VALUES ({','.join('?' for _ in values)})", list(values.values()))
                    operation = 'inserido'
                    result['inseridos'] += 1
                db.execute('INSERT INTO carga_realizado_auditoria VALUES (?,?,?,?,?)',
                           (digest, identifier, operation, json.dumps(old, ensure_ascii=False) if old else None,
                            json.dumps(record, ensure_ascii=False)))
            for old in unmatched:
                if not start.isoformat() <= old['data_emissao'] <= end.isoformat():
                    continue
                db.execute('INSERT INTO carga_realizado_auditoria VALUES (?,?,?,?,NULL)',
                           (digest, old['id_item_erp'], 'substituido', json.dumps(old, ensure_ascii=False)))
                db.execute('DELETE FROM fato_item_orcamento WHERE id_item_erp=?', (old['id_item_erp'],))
                result['substituidos'] += 1
        preserved = [row[0] for row in db.execute('SELECT id_item_erp FROM fato_item_orcamento WHERE data_emissao BETWEEN ? AND ?', (start.isoformat(), end.isoformat()))]
        added = {row[0] for row in db.execute("SELECT id_item FROM carga_realizado_auditoria WHERE carga_hash=? AND operacao='inserido'", (digest,))}
        result['fora_do_arquivo_preservados'] = len(set(preserved) - seen - added)
        result['ultima_data'] = db.execute('SELECT MAX(data_emissao) FROM fato_item_orcamento').fetchone()[0]
        result['totais_periodo'] = [list(row) for row in db.execute('''
            SELECT substr(data_emissao,1,7),COUNT(*),SUM(valor_liquido_centavos)
            FROM fato_item_orcamento WHERE data_emissao BETWEEN ? AND ? GROUP BY 1 ORDER BY 1
        ''', (start.isoformat(), end.isoformat()))]
        db.execute('INSERT INTO carga_realizado VALUES (?,?,?)',
                   (digest, datetime.now(timezone.utc).isoformat(), json.dumps(result, ensure_ascii=False)))
        return result
