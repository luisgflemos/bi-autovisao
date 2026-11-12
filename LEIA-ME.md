# Deploy do BI de Vendas

O pacote contém a aplicação e os recursos visuais. A base SQLite é entregue separadamente. O BI usa Python 3.12 e a biblioteca padrão.

1. Extraia este ZIP no servidor. Extraia o ZIP privado ao lado de `compose.yaml`. O caminho resultante deve ser `dados/vendas.sqlite3`.
2. Em um host Linux com Docker, conceda ao usuário do contêiner acesso de escrita à pasta e à base com `sudo chown -R 10001:10001 dados`. O BI atualiza o calendário de dias úteis no SQLite quando necessário.
3. Execute `docker compose up -d --build`. Confira os logs com `docker compose logs --tail=50 bi` e valide com `curl -fsS http://127.0.0.1:8765/api/visao -o /dev/null`.
4. Configure o proxy reverso no mesmo host para encaminhar um domínio dedicado a `http://127.0.0.1:8765`. Aplique HTTPS e autenticação corporativa a todas as rotas, inclusive `/api/`, antes de liberar o acesso. O BI não tem login próprio.
5. Confira as cinco abas no navegador e teste um filtro e a opção de salvar em PDF. Configure backups do SQLite com a API de backup do SQLite ou com o serviço parado.

Para rodar sem Docker, defina `BI_DATABASE` com o caminho absoluto do SQLite e execute `python app/painel.py`. O padrão é escutar somente em `127.0.0.1:8765`. Use `BI_PORT` se precisar de outra porta.

O ZIP privado contém dados comerciais. Transfira-o pelo canal corporativo autorizado e mantenha-o fora do Git e da pasta pública do proxy.

## Login (usuário e senha por pessoa)

O BI exige login sempre que escuta fora do `localhost`. Configure no servidor:

- `BI_USUARIOS`: uma entrada por pessoa, separadas por vírgula, no formato `usuario:hash`. Gere cada entrada com `python app/painel.py --gerar-usuario NOME` (a senha é pedida sem aparecer na tela e precisa ter pelo menos 10 caracteres). Para remover alguém, apague a entrada e reinicie.
- `BI_SESSAO_SEGREDO`: texto aleatório longo que assina os cookies de sessão. Trocar o valor desconecta todo mundo.
- `BI_SESSAO_HORAS` (opcional, padrão 12): duração da sessão.

Após 5 senhas erradas em 15 minutos, o usuário e o IP ficam bloqueados por 15 minutos. A página de login, o banner `/og-banner.png`, a logo, as fontes e `/saude` ficam públicos para o link preview funcionar. Os dados e a `/api/` exigem login.

## Carga da base em servidor sem acesso a arquivos

Com `BI_TOKEN_IMPORTACAO` definido, o BI inicia mesmo sem a base e aceita uma carga única:

```
curl -X PUT -H "Authorization: Bearer $BI_TOKEN_IMPORTACAO" --data-binary @dados/vendas.sqlite3 https://bi.autovisao.com/admin/base
```

A base é validada antes de substituir a atual. Remova `BI_TOKEN_IMPORTACAO` depois da carga para desligar a rota.

## Usuários no banco

Os logins ficam na tabela `usuario` de `usuarios.sqlite3`, ao lado da base de vendas (no Railway: `/data/usuarios.sqlite3`). O arquivo é separado para que uma nova carga de vendas não apague os usuários. Para criar ou trocar a senha de alguém:

```
BI_DATABASE=dados/vendas.sqlite3 python app/painel.py --criar-usuario NOME admin
```

O papel pode ser `admin` ou `leitor`. Para subir o arquivo ao servidor, use `PUT /admin/usuarios` com o mesmo token da carga da base.

## Hospedagem: site na Vercel, API e bancos no Railway

- **Railway** roda `app/painel.py` pelo `Dockerfile`, com volume em `/data` (base de vendas e usuários).
- **Vercel** publica as telas estáticas (`web/build.sh` gera `dist/`) e repassa `/api/*`, `/login` e `/logout` ao Railway pelo `vercel.json`. Como tudo passa pelo mesmo domínio, o cookie de login funciona sem CORS.
- O domínio `bi.autovisao.com` aponta para a Vercel. Se a URL do Railway mudar, atualize as três rotas em `vercel.json`.
