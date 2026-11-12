"""Autenticação do BI: usuários com senha própria e sessão em cookie assinado.

Usuários vêm da variável BI_USUARIOS, uma entrada por usuário separada por
vírgula ou quebra de linha, no formato ``usuario:pbkdf2_sha256$iteracoes$sal$hash``.
Gere uma entrada com ``python painel.py --gerar-usuario NOME``.

Também lê a tabela ``usuario`` do banco indicado em BI_USUARIOS_DB (padrão:
``usuarios.sqlite3`` ao lado da base de vendas). Esse banco é separado da base
de vendas para que uma nova carga de dados não apague os logins.
"""
import base64
import hashlib
import hmac
import os
import secrets
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
import threading
import time

ITERACOES = 390_000
DURACAO_SESSAO = int(os.environ.get("BI_SESSAO_HORAS", "12")) * 3600
COOKIE = "av_sessao"
MAX_FALHAS = 5
JANELA_FALHAS = 15 * 60


def gerar_hash(senha, iteracoes=ITERACOES):
    sal = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(sal), iteracoes).hex()
    return f"pbkdf2_sha256${iteracoes}${sal}${digest}"


def conferir_hash(senha, armazenado):
    try:
        algoritmo, iteracoes, sal, esperado = armazenado.split("$")
        if algoritmo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(sal), int(iteracoes)).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, esperado)


def carregar_usuarios(texto):
    usuarios = {}
    for entrada in texto.replace("\n", ",").split(","):
        entrada = entrada.strip()
        if not entrada:
            continue
        nome, _, armazenado = entrada.partition(":")
        if nome and armazenado:
            usuarios[nome.strip().lower()] = armazenado.strip()
    return usuarios


# Hash fictício usado quando o usuário não existe, para o tempo de resposta não revelar quem existe.
_HASH_FICTICIO = gerar_hash(secrets.token_hex(8), ITERACOES)


ESQUEMA_USUARIOS = """
CREATE TABLE IF NOT EXISTS usuario (
    nome TEXT PRIMARY KEY NOT NULL,          -- sempre em minúsculas
    hash_senha TEXT NOT NULL,                -- pbkdf2_sha256$iteracoes$sal$hash
    papel TEXT NOT NULL DEFAULT 'leitor' CHECK (papel IN ('admin', 'leitor')),
    ativo INTEGER NOT NULL DEFAULT 1 CHECK (ativo IN (0, 1)),
    criado_em TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def gravar_usuario(caminho, nome, senha, papel="leitor"):
    with closing(sqlite3.connect(caminho)) as db, db:
        db.executescript(ESQUEMA_USUARIOS)
        db.execute(
            "INSERT INTO usuario(nome, hash_senha, papel) VALUES (?,?,?) "
            "ON CONFLICT(nome) DO UPDATE SET hash_senha=excluded.hash_senha, papel=excluded.papel, ativo=1",
            (nome.strip().lower(), gerar_hash(senha), papel),
        )


def ler_usuarios_db(caminho):
    if not caminho or not Path(caminho).is_file():
        return {}
    try:
        with closing(sqlite3.connect(Path(caminho).as_uri() + "?mode=ro", uri=True)) as db:
            return {nome.lower(): hash_senha for nome, hash_senha in
                    db.execute("SELECT nome, hash_senha FROM usuario WHERE ativo=1")}
    except sqlite3.DatabaseError as error:
        print(f"Aviso: não consegui ler {caminho}: {error}", file=sys.stderr, flush=True)
        return {}


class Acesso:
    def __init__(self, ambiente=os.environ, banco_usuarios=None):
        self._usuarios_env = carregar_usuarios(ambiente.get("BI_USUARIOS", ""))
        self.banco_usuarios = ambiente.get("BI_USUARIOS_DB") or banco_usuarios
        self._cache_db = (None, {})
        segredo = ambiente.get("BI_SESSAO_SEGREDO", "")
        if not segredo and self.ativo:
            print("Aviso: BI_SESSAO_SEGREDO ausente; as sessões expiram a cada reinício.", file=sys.stderr, flush=True)
        self.segredo = (segredo or secrets.token_hex(32)).encode()
        self.cookie_seguro = ambiente.get("BI_COOKIE_SEGURO", "1") != "0"
        self._falhas = {}
        self._trava = threading.Lock()

    @property
    def usuarios(self):
        """Usuários do ambiente e do banco; o banco é relido quando o arquivo muda."""
        caminho = self.banco_usuarios
        try:
            marca = Path(caminho).stat().st_mtime_ns if caminho else None
        except OSError:
            marca = None
        if marca != self._cache_db[0]:
            self._cache_db = (marca, ler_usuarios_db(caminho))
        return {**self._usuarios_env, **self._cache_db[1]}

    @property
    def ativo(self):
        return bool(self.usuarios)

    # ---- sessão ----
    def _assinar(self, carga):
        return hmac.new(self.segredo, carga.encode(), hashlib.sha256).hexdigest()

    def criar_sessao(self, usuario):
        expira = int(time.time()) + DURACAO_SESSAO
        carga = base64.urlsafe_b64encode(f"{usuario}|{expira}".encode()).decode().rstrip("=")
        return f"{carga}.{self._assinar(carga)}"

    def usuario_da_sessao(self, cabecalho_cookie):
        if not cabecalho_cookie:
            return None
        valor = None
        for parte in cabecalho_cookie.split(";"):
            nome, _, conteudo = parte.strip().partition("=")
            if nome == COOKIE:
                valor = conteudo
        if not valor or "." not in valor:
            return None
        carga, assinatura = valor.rsplit(".", 1)
        if not hmac.compare_digest(self._assinar(carga), assinatura):
            return None
        try:
            texto = base64.urlsafe_b64decode(carga + "=" * (-len(carga) % 4)).decode()
            usuario, expira = texto.rsplit("|", 1)
            if int(expira) < time.time():
                return None
        except ValueError:
            return None
        return usuario if usuario in self.usuarios else None

    def cabecalho_cookie(self, valor, max_age=DURACAO_SESSAO):
        atributos = [f"{COOKIE}={valor}", "Path=/", "HttpOnly", "SameSite=Lax", f"Max-Age={max_age}"]
        if self.cookie_seguro:
            atributos.append("Secure")
        return "; ".join(atributos)

    # ---- login com limite de tentativas ----
    def _bloqueado(self, chave, agora):
        tentativas = [t for t in self._falhas.get(chave, []) if agora - t < JANELA_FALHAS]
        self._falhas[chave] = tentativas
        return len(tentativas) >= MAX_FALHAS

    def autenticar(self, usuario, senha, ip):
        """Devolve (usuario_normalizado | None, mensagem_de_erro | None)."""
        usuario = (usuario or "").strip().lower()
        chaves = (f"ip:{ip}", f"usuario:{usuario}")
        agora = time.time()
        with self._trava:
            if any(self._bloqueado(chave, agora) for chave in chaves):
                return None, "Muitas tentativas. Aguarde 15 minutos e tente novamente."
        armazenado = self.usuarios.get(usuario)
        valido = conferir_hash(senha or "", armazenado or _HASH_FICTICIO) and armazenado is not None
        with self._trava:
            if valido:
                for chave in chaves:
                    self._falhas.pop(chave, None)
                return usuario, None
            for chave in chaves:
                self._falhas.setdefault(chave, []).append(agora)
        return None, "Usuário ou senha inválidos."


def gerar_usuario_cli(nome):
    import getpass
    senha = os.environ.get("BI_NOVA_SENHA") or getpass.getpass(f"Senha para {nome}: ")
    if len(senha) < 10:
        raise SystemExit("Use uma senha com pelo menos 10 caracteres.")
    print(f"{nome.strip().lower()}:{gerar_hash(senha)}")
