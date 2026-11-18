"""Lê o padrão de exportação Consulta Pedidos Completo sem importar o rodapé."""
import hashlib
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from io import BytesIO
from zipfile import ZipFile, BadZipFile


def workbook_payload(content):
    from openpyxl import load_workbook

    try:
        with ZipFile(BytesIO(content)) as archive:
            if sum(item.file_size for item in archive.infolist()) > 128 * 1024 ** 2:
                raise ValueError('planilha descompactada excede 128 MB')
    except BadZipFile:
        raise ValueError('arquivo XLSX inválido ou incompleto') from None
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True)
    try:
        sheet = workbook.active
        sheet.reset_dimensions()
        rows = iter(sheet.values)
        headers = next(rows)
        required = {0: 'Base', 3: 'Emissao', 5: 'Pedido', 9: 'Cliente', 22: 'Qtde', 24: 'Valor Total'}
        if any(index >= len(headers) or headers[index] != title for index, title in required.items()):
            raise ValueError('use a exportação 1.1 - Consulta Pedidos Completo, no padrão de 31 colunas')

        def text(value):
            return ' '.join(str(value).split()) if value is not None else None

        def flag(value):
            key = str(value or '').strip().lower()
            if key not in ('sim', 'nao', 'não', ''):
                raise ValueError('indicador Sim/Não inválido')
            return {'sim': 1, 'nao': 0, 'não': 0, '': None}[key]

        items = []
        for number, source in enumerate(rows, 2):
            row = tuple(source) + (None,) * max(0, 31 - len(source))
            if not row[3]:
                if all(value is None for value in row[:22]):
                    continue
                raise ValueError(f'linha {number} sem data')
            if len(items) >= 100000:
                raise ValueError('a planilha excede 100 mil itens')
            if row[22] != 1 or not row[5] or not row[21]:
                raise ValueError(f'linha {number} sem pedido/serviço ou com quantidade diferente de 1')
            try:
                day = (row[3] if isinstance(row[3], datetime) else datetime.strptime(str(row[3]), '%d/%m/%Y')).date().isoformat()
                cents = int((Decimal(str(row[24] or 0)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
            except (ValueError, InvalidOperation, OverflowError):
                raise ValueError(f'data ou valor inválido na linha {number}') from None
            items.append({
                'unidade': text(row[0]), 'item_faturado': flag(row[1]),
                'periodicidade': text(row[2]), 'data_emissao': day, 'vendedor': text(row[4]),
                'pedido': str(row[5]).zfill(10), 'agrupamento': text(row[6]),
                'segmento': text(row[7]), 'macro_regiao': text(row[8]), 'cliente': text(row[9]),
                'tipo_veiculo': text(row[10]), 'marca_veiculo': text(row[11]), 'modelo_veiculo': text(row[12]),
                'servico': text(row[21]), 'quantidade': 1, 'valor_liquido_centavos': cents,
                'vistoriador': text(row[26]), 'vistoria_movel': flag(row[27]), 'condicao_pagamento': text(row[29]),
            })
        if not items:
            raise ValueError('planilha sem serviços')
        return {'inicio': min(row['data_emissao'] for row in items),
                'fim': max(row['data_emissao'] for row in items),
                'arquivo_sha256': hashlib.sha256(content).hexdigest(), 'linhas': items}
    finally:
        workbook.close()
