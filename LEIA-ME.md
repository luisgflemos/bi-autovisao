# Deploy do BI de Vendas

O pacote contém a aplicação e os recursos visuais. A base SQLite é entregue separadamente. O BI usa Python 3.12 e a biblioteca padrão.

1. Extraia este ZIP no servidor. Extraia o ZIP privado ao lado de `compose.yaml`. O caminho resultante deve ser `dados/vendas.sqlite3`.
2. Em um host Linux com Docker, conceda ao usuário do contêiner acesso de escrita à pasta e à base com `sudo chown -R 10001:10001 dados`. O BI atualiza o calendário de dias úteis no SQLite quando necessário.
3. Execute `docker compose up -d --build`. Confira os logs com `docker compose logs --tail=50 bi` e valide com `curl -fsS http://127.0.0.1:8765/api/visao -o /dev/null`.
4. Configure o proxy reverso no mesmo host para encaminhar um domínio dedicado a `http://127.0.0.1:8765`. Aplique HTTPS e autenticação corporativa a todas as rotas, inclusive `/api/`, antes de liberar o acesso. O BI não tem login próprio.
5. Confira as cinco abas no navegador e teste um filtro e a opção de salvar em PDF. Configure backups do SQLite com a API de backup do SQLite ou com o serviço parado.

Para rodar sem Docker, defina `BI_DATABASE` com o caminho absoluto do SQLite e execute `python app/painel.py`. O padrão é escutar somente em `127.0.0.1:8765`. Use `BI_PORT` se precisar de outra porta.

O ZIP privado contém dados comerciais. Transfira-o pelo canal corporativo autorizado e mantenha-o fora do Git e da pasta pública do proxy.
