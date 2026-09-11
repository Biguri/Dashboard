"""Autenticação Supabase e contexto de clínica de uma sessão."""

from dataclasses import dataclass
import logging
import re
from typing import Mapping, NamedTuple


DOMINIO_LOGIN = "login.dashboard.local"
PADRAO_USUARIO = re.compile(r"[A-Za-z0-9_.-]{1,64}")
LOGGER = logging.getLogger(__name__)


def _categoria_erro_auth(erro: Exception) -> str:
    codigo = getattr(erro, "code", None)
    if codigo in {"invalid_credentials", "email_not_confirmed", "over_request_rate_limit"}:
        return codigo
    mensagem = str(getattr(erro, "message", "")).lower()
    if "invalid login credentials" in mensagem:
        return "invalid_credentials"
    if "invalid" in mensagem and "email" in mensagem:
        return "invalid_email"
    if "email not confirmed" in mensagem:
        return "email_not_confirmed"
    return "other"


class ConfiguracaoInvalida(ValueError):
    pass


class AcessoNegado(RuntimeError):
    def __init__(self, mensagem: str, etapa: str = "desconhecida"):
        super().__init__(mensagem)
        self.etapa = etapa


class ConfiguracaoSupabase(NamedTuple):
    url: str
    publishable_key: str


@dataclass(frozen=True)
class SessaoSupabase:
    usuario: str
    user_id: str
    clinica_id: str
    clinica_nome: str
    access_token: str
    refresh_token: str


def normalizar_usuario(usuario: str) -> str:
    if not isinstance(usuario, str):
        raise ValueError("Nome de usuário inválido.")
    normalizado = usuario.strip().lower()
    if PADRAO_USUARIO.fullmatch(normalizado) is None:
        raise ValueError("Nome de usuário inválido.")
    return normalizado


def email_tecnico(usuario: str) -> str:
    return f"{normalizar_usuario(usuario)}@{DOMINIO_LOGIN}"


def carregar_configuracao(valores: Mapping[str, object]) -> ConfiguracaoSupabase:
    url = valores.get("SUPABASE_URL")
    chave = valores.get("SUPABASE_PUBLISHABLE_KEY")
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ConfiguracaoInvalida("Configuração do Supabase indisponível.")
    if not isinstance(chave, str) or not chave.startswith("sb_publishable_"):
        raise ConfiguracaoInvalida("Configure somente a chave publicável do Supabase.")
    return ConfiguracaoSupabase(url.rstrip("/"), chave)


def criar_cliente(configuracao: ConfiguracaoSupabase):
    from supabase import create_client

    return create_client(configuracao.url, configuracao.publishable_key)


def _carregar_perfil(cliente, user_id: str, usuario: str, auth_response) -> SessaoSupabase:
    resposta = (
        cliente.table("perfis")
        .select("user_id,clinica_id,nome_usuario,clinicas(nome)")
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    perfil = resposta.data
    clinica = perfil.get("clinicas") if isinstance(perfil, dict) else None
    if (not isinstance(perfil, dict) or perfil.get("user_id") != user_id
            or perfil.get("nome_usuario") != usuario or not perfil.get("clinica_id")
            or not isinstance(clinica, dict) or not isinstance(clinica.get("nome"), str)):
        raise AcessoNegado("Conta sem clínica autorizada.")
    sessao = getattr(auth_response, "session", None)
    access_token = getattr(sessao, "access_token", None)
    refresh_token = getattr(sessao, "refresh_token", None)
    if not isinstance(access_token, str) or not isinstance(refresh_token, str):
        raise AcessoNegado("Sessão de acesso inválida.")
    return SessaoSupabase(
        usuario=usuario,
        user_id=user_id,
        clinica_id=perfil["clinica_id"],
        clinica_nome=clinica["nome"],
        access_token=access_token,
        refresh_token=refresh_token,
    )


def _validar_usuario(cliente, usuario: str, auth_response) -> SessaoSupabase:
    validado = cliente.auth.get_user()
    user_id = getattr(getattr(validado, "user", None), "id", None)
    if not isinstance(user_id, str):
        raise AcessoNegado("Sessão de acesso inválida.")
    return _carregar_perfil(cliente, user_id, usuario, auth_response)


def entrar(cliente, usuario: str, senha: str) -> SessaoSupabase:
    try:
        normalizado = normalizar_usuario(usuario)
        if not isinstance(senha, str) or not 1 <= len(senha) <= 1024:
            raise AcessoNegado("Credenciais inválidas.")
    except (AcessoNegado, ValueError):
        raise AcessoNegado("Acesso não autorizado.", "entrada") from None
    try:
        resposta = cliente.auth.sign_in_with_password({
            "email": email_tecnico(normalizado),
            "password": senha,
        })
    except Exception as erro:
        status = getattr(erro, "status", "desconhecido")
        if not isinstance(status, int):
            status = "desconhecido"
        LOGGER.warning(
            "Login recusado na etapa Auth (status %s, categoria %s).",
            status,
            _categoria_erro_auth(erro),
        )
        raise AcessoNegado("Acesso não autorizado.", "auth") from None
    try:
        return _validar_usuario(cliente, normalizado, resposta)
    except Exception as erro:
        codigo = getattr(erro, "code", None)
        if not isinstance(codigo, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,32}", codigo):
            codigo = "desconhecido"
        LOGGER.warning(
            "Login recusado na etapa de perfil/RLS (código %s).", codigo
        )
        try:
            cliente.auth.sign_out()
        except Exception:
            pass
        if isinstance(erro, AcessoNegado):
            raise AcessoNegado(str(erro), "perfil_rls") from None
        raise AcessoNegado("Acesso não autorizado.", "perfil_rls") from None


def restaurar(cliente, usuario: str, access_token: str, refresh_token: str) -> SessaoSupabase:
    try:
        normalizado = normalizar_usuario(usuario)
        if not all(isinstance(token, str) and token for token in (access_token, refresh_token)):
            raise AcessoNegado("Sessão de acesso inválida.")
        resposta = cliente.auth.set_session(access_token, refresh_token)
        return _validar_usuario(cliente, normalizado, resposta)
    except Exception as erro:
        if isinstance(erro, AcessoNegado):
            raise AcessoNegado(str(erro), "restauracao") from None
        raise AcessoNegado("Sessão expirada ou revogada.", "restauracao") from None


def sair(cliente) -> None:
    try:
        cliente.auth.sign_out()
    except Exception:
        pass
