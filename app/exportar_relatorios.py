"""Pacote de PDFs gerado sobre uma cópia consistente das bases."""
import json
import os
import re
import sqlite3
import threading
import unicodedata
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from zipfile import ZipFile, ZIP_DEFLATED


def filename(value):
    value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode()
    return re.sub(r'[^A-Za-z0-9_-]+', '_', value).strip('_') or 'Sem_nome'


def report_plan(db, joins, dimensions):
    cutoff = db.execute('SELECT MAX(data_emissao) FROM fato_item_orcamento').fetchone()[0]
    if not cutoff:
        raise ValueError('base sem vendas')
    start = cutoff[:7] + '-01'
    sellers = [row[0] for row in db.execute(
        f"SELECT DISTINCT {dimensions['vendedor']} {joins} WHERE f.data_emissao BETWEEN ? AND ? ORDER BY 1",
        (start, cutoff),
    )]
    units = [row[0] for row in db.execute(
        f"SELECT DISTINCT {dimensions['unidade']} {joins} WHERE f.data_emissao BETWEEN ? AND ? ORDER BY 1",
        (start, cutoff),
    )]
    stamp = cutoff.replace('-', '')
    reports = []
    for seller in sellers:
        reports.append({'tipo': 'cliente', 'nome': seller, 'arquivo': f'VisaoCliente/{filename(seller)}_{stamp}.pdf',
                        'url': '/relatorio-cliente?' + urlencode({'vendedor': seller, 'fim': cutoff})})
    reports.extend([
        {'tipo': 'gerencial', 'nome': 'Visão Gerencial', 'arquivo': f'VisaoGerencial/VisaoGerencial_{stamp}.pdf', 'url': '/gerencial'},
        {'tipo': 'vistoriadores', 'nome': 'Visão por Vistoriadores', 'arquivo': f'VisaoVistoriadores/VisaoVistoriadores_{stamp}.pdf', 'url': '/vistoriadores'},
    ])
    for unit in units:
        reports.append({'tipo': 'speed', 'nome': unit, 'arquivo': f'SpeedDiario/{filename(unit)}_{stamp}.pdf',
                        'url': '/speed?' + urlencode({'unidade': unit, 'fim': cutoff})})
    reports.append({'tipo': 'speed', 'nome': 'Total', 'arquivo': f'SpeedDiario/total_{stamp}.pdf',
                    'url': '/speed?' + urlencode({'fim': cutoff})})
    names = [report['arquivo'].casefold() for report in reports]
    if len(names) != len(set(names)):
        raise ValueError('há nomes de arquivos repetidos; revise os nomes dos vendedores e unidades')
    return {'corte': cutoff, 'inicio': start, 'vendedores': sellers, 'unidades': units, 'relatorios': reports}


class Exports:
    def __init__(self):
        self.lock = threading.Lock()
        self.jobs = {}

    def start(self, owner, sales_path, target_path, base_url, api):
        with self.lock:
            active = next((job for job in self.jobs.values() if job['estado'] in ('preparando', 'gerando')), None)
            if active:
                if active['usuario'] == owner:
                    return self.public(active)
                raise ValueError('há uma exportação em andamento; aguarde a conclusão')
            identifier = uuid.uuid4().hex
            folder = sales_path.parent / 'exportacoes' / identifier
            folder.mkdir(parents=True)
            job = {'id': identifier, 'usuario': owner, 'estado': 'preparando', 'concluidos': 0,
                   'total': 0, 'atual': '', 'pasta': folder, 'criado_em': datetime.now(timezone.utc).isoformat()}
            self.jobs[identifier] = job
            threading.Thread(target=self.run, args=(job, sales_path, target_path, base_url, api), daemon=True).start()
            return self.public(job)

    def public(self, job):
        return {key: value for key, value in job.items() if key != 'pasta'}

    def get(self, identifier, owner):
        with self.lock:
            job = self.jobs.get(identifier)
            if not job or job['usuario'] != owner:
                return None
            return self.public(job)

    def archive(self, identifier, owner):
        with self.lock:
            job = self.jobs.get(identifier)
            if not job or job['usuario'] != owner or job['estado'] != 'concluido':
                return None
            return job['pasta'] / job['arquivo']

    def update(self, job, **changes):
        with self.lock:
            job.update(changes)

    def run(self, job, sales_path, target_path, base_url, api):
        folder = job['pasta']
        sales_copy = folder / 'vendas.sqlite3'
        targets_copy = folder / 'metas.sqlite3'
        try:
            for source_path, destination in ((sales_path, sales_copy), (target_path, targets_copy)):
                if source_path.is_file():
                    with closing(sqlite3.connect(source_path.resolve().as_uri() + '?mode=ro', uri=True)) as source:
                        with closing(sqlite3.connect(destination)) as output:
                            source.backup(output)

            def database():
                connection = sqlite3.connect(sales_copy.resolve().as_uri() + '?mode=ro', uri=True)
                if targets_copy.is_file():
                    connection.execute('ATTACH DATABASE ? AS metas', (targets_copy.resolve().as_uri() + '?mode=ro',))
                return connection

            with closing(database()) as db:
                joins, _ = api.sales_joins(db)
                plan = report_plan(db, joins, api.DIMENSIONS)
            self.update(job, estado='gerando', total=len(plan['relatorios']), corte=plan['corte'])

            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(args=['--disable-dev-shm-usage'])
                try:
                    context = browser.new_context(viewport={'width': 1600, 'height': 1000}, locale='pt-BR',
                                                  timezone_id='America/Sao_Paulo')
                    cookie = api.ACESSO.criar_sessao(job['usuario'])
                    context.add_cookies([{'name': 'av_sessao', 'value': cookie, 'url': base_url, 'httpOnly': True}])
                    page = context.new_page()
                    current_data = {}

                    def intercept(route):
                        path = urlsplit(route.request.url).path
                        if path == '/api/sessao':
                            data = {'usuario': job['usuario']}
                        else:
                            data = current_data.get(path)
                        if data is None:
                            route.abort()
                        else:
                            route.fulfill(status=200, content_type='application/json',
                                          body=json.dumps(data, ensure_ascii=False))

                    page.route('**/api/**', intercept)
                    page.emulate_media(media='print')
                    for report in plan['relatorios']:
                        self.update(job, atual=report['nome'])
                        query = {'fim': [plan['corte']]}
                        with closing(database()) as db:
                            if report['tipo'] == 'cliente':
                                query['vendedor'] = [report['nome']]
                                payload = api.customer_report(db, query)
                                endpoint = '/api/relatorio-cliente'
                            elif report['tipo'] == 'speed':
                                if report['nome'] != 'Total':
                                    query['unidade'] = [report['nome']]
                                payload = api.speed_report(db, query)
                                endpoint = '/api/speed'
                            else:
                                payload = api.management_report(db, {'ano': [plan['corte'][:4]]},
                                                                inspectors=report['tipo'] == 'vistoriadores')
                                endpoint = '/api/vistoriadores' if report['tipo'] == 'vistoriadores' else '/api/gerencial'
                        current_data.clear()
                        current_data[endpoint] = payload
                        page.goto(base_url + report['url'], wait_until='networkidle', timeout=60000)
                        ready = '#imprimir' if report['tipo'] in ('cliente', 'speed') else '#print'
                        page.wait_for_selector(ready + ':not([disabled])', state='attached', timeout=60000)
                        if report['tipo'] in ('cliente', 'speed'):
                            page.locator('#executivo').evaluate('(node) => { node.open = true; }')
                        page.evaluate('document.fonts.ready')
                        output = folder / report['arquivo']
                        output.parent.mkdir(exist_ok=True)
                        page.pdf(path=str(output), format='A4', landscape=True, print_background=True,
                                 prefer_css_page_size=True)
                        self.update(job, concluidos=job['concluidos'] + 1)
                finally:
                    browser.close()
            archive_name = 'Relatorios_' + plan['corte'].replace('-', '') + '.zip'
            temporary = folder / (archive_name + '.tmp')
            with ZipFile(temporary, 'w', compression=ZIP_DEFLATED) as archive:
                for report in plan['relatorios']:
                    archive.write(folder / report['arquivo'], report['arquivo'])
            os.replace(temporary, folder / archive_name)
            self.update(job, estado='concluido', atual='', arquivo=archive_name)
        except Exception as error:
            print(f'Falha na exportação {job["id"]}: {error}', flush=True)
            self.update(job, estado='erro', erro='Não foi possível concluir os PDFs. Tente novamente ou consulte os logs da aplicação.')
        finally:
            sales_copy.unlink(missing_ok=True)
            targets_copy.unlink(missing_ok=True)


EXPORTS = Exports()
