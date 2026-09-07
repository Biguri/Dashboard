"""Lista de acesso, hashes de senha, limitação de tentativas e sessões."""

import hashlib
from contextlib import closing
import hmac
import json
import math
from pathlib import Path
import re
import secrets
import sqlite3
import time

ITERACOES = 600_000
INATIVIDADE = 30 * 60
DURACAO_MAXIMA = 8 * 60 * 60
JANELA_TENTATIVAS = 15 * 60
MAX_TENTATIVAS = 5
DURACAO_BLOQUEIO = 5 * 60
PADRAO_HASH = re.compile(r"pbkdf2_sha256\$600000\$[0-9a-f]{32}\$[0-9a-f]{64}")
HASH_FICTICIO = f"pbkdf2_sha256${ITERACOES}$" + "0" * 32 + "$" + "0" * 64


def usuario_valido(usuario) -> bool:
    return isinstance(usuario, str) and re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", usuario) is not None


def gerar_hash(senha: str) -> str:
    if not isinstance(senha, str) or not 12 <= len(senha) <= 1024:
        raise ValueError("Use uma senha entre 12 e 1024 caracteres.")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), salt, ITERACOES)
    return f"pbkdf2_sha256${ITERACOES}${salt.hex()}${digest.hex()}"


def verificar_senha(senha, hash_armazenado) -> bool:
    if not isinstance(senha, str) or not 1 <= len(senha) <= 1024:
        return False
    if not isinstance(hash_armazenado, str) or not PADRAO_HASH.fullmatch(hash_armazenado):
        return False
    _, _, salt, esperado = hash_armazenado.split("$")
    calculado = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), bytes.fromhex(salt), ITERACOES)
    return hmac.compare_digest(calculado, bytes.fromhex(esperado))


def carregar_usuarios(arquivo: Path, config_json=None) -> dict[str, str]:
    """Sem configuração não existe acesso; configuração inválida gera erro."""
    if config_json is None:
        if not arquivo.exists():
            return {}
        config_json = arquivo.read_text(encoding="utf-8")
    try:
        usuarios = json.loads(config_json)
    except (ValueError, TypeError) as erro:
        raise ValueError("Configuração de usuários inválida.") from erro
    if not isinstance(usuarios, dict):
        raise ValueError("A configuração deve conter um objeto de usuários e hashes.")
    for usuario, hash_senha in usuarios.items():
        if (not usuario_valido(usuario) or not isinstance(hash_senha, str)
                or not PADRAO_HASH.fullmatch(hash_senha)):
            raise ValueError("Usuário ou hash inválido na configuração.")
    return usuarios


def validar_login(usuario, senha, usuarios: dict, banco: Path, agora=None) -> bool:
    """Bloqueio por conta compartilhado entre sessões deste servidor.

    O SQLite usa uma transação para que tentativas simultâneas sejam contadas.
    O arquivo deve persistir e ser compartilhado para sustentar o bloqueio
    entre reinicializações e múltiplas instâncias.
    """
    if not usuario_valido(usuario) or not isinstance(senha, str) or len(senha) > 1024:
        return False
    agora = time.time() if agora is None else agora
    banco.parent.mkdir(parents=True, exist_ok=True)
    chave = hashlib.sha256(usuario.encode()).hexdigest()
    with closing(sqlite3.connect(banco, timeout=15)) as conexao, conexao:
        conexao.execute("CREATE TABLE IF NOT EXISTS tentativas (chave TEXT PRIMARY KEY, falhas INTEGER, inicio REAL)")
        conexao.execute("BEGIN IMMEDIATE")
        campos = {linha[1] for linha in conexao.execute("PRAGMA table_info(tentativas)")}
        if "bloqueado_ate" not in campos:
            conexao.execute("ALTER TABLE tentativas ADD COLUMN bloqueado_ate REAL NOT NULL DEFAULT 0")
            # Migra bloqueios antigos ainda ativos para a nova duração.
            conexao.execute("DELETE FROM tentativas WHERE inicio <= ?", (agora - JANELA_TENTATIVAS,))
            conexao.execute(
                "UPDATE tentativas SET bloqueado_ate = ? WHERE falhas >= ?",
                (agora + DURACAO_BLOQUEIO, MAX_TENTATIVAS),
            )
        conexao.execute(
            "DELETE FROM tentativas WHERE (bloqueado_ate > 0 AND bloqueado_ate <= ?) "
            "OR (bloqueado_ate = 0 AND inicio <= ?)",
            (agora, agora - JANELA_TENTATIVAS),
        )
        linha = conexao.execute(
            "SELECT falhas, inicio, bloqueado_ate FROM tentativas WHERE chave = ?", (chave,)
        ).fetchone()
        falhas, inicio, bloqueado_ate = linha if linha else (0, agora, 0)
        if bloqueado_ate > agora:
            return False
        hash_senha = usuarios.get(usuario, HASH_FICTICIO)
        verificada = verificar_senha(senha, hash_senha)
        if verificada and usuario in usuarios:
            conexao.execute("DELETE FROM tentativas WHERE chave = ?", (chave,))
            return True
        falhas += 1
        bloqueado_ate = agora + DURACAO_BLOQUEIO if falhas >= MAX_TENTATIVAS else 0
        conexao.execute(
            "INSERT OR REPLACE INTO tentativas (chave, falhas, inicio, bloqueado_ate) VALUES (?, ?, ?, ?)",
            (chave, falhas, inicio, bloqueado_ate),
        )
        return False


def iniciar_sessao(estado, usuario, hash_senha, agora=None) -> None:
    agora = time.time() if agora is None else agora
    estado.clear()
    estado.update({
        "autenticado": True,
        "usuario": usuario,
        "credencial_versao": hashlib.sha256(hash_senha.encode()).hexdigest(),
        "inicio_sessao": agora,
        "ultima_atividade": agora,
    })


def sessao_autorizada(estado, usuarios: dict, agora=None, atualizar=True) -> bool:
    """Revalida a autorização e remove os dados ao expirar ou revogar acesso."""
    if estado.get("autenticado") is not True:
        return False
    agora = time.time() if agora is None else agora
    usuario = estado.get("usuario")
    hash_senha = usuarios.get(usuario) if isinstance(usuario, str) else None
    inicio = estado.get("inicio_sessao")
    ultima = estado.get("ultima_atividade")
    tempos_validos = all(isinstance(v, (int, float)) and math.isfinite(v) for v in (inicio, ultima))
    versao = hashlib.sha256(hash_senha.encode()).hexdigest() if isinstance(hash_senha, str) else None
    valido = (
        versao is not None and estado.get("credencial_versao") == versao
        and tempos_validos and inicio <= ultima <= agora
        and agora - inicio < DURACAO_MAXIMA and agora - ultima < INATIVIDADE
    )
    if not valido:
        estado.clear()
        return False
    if atualizar:
        estado["ultima_atividade"] = agora
    return True
