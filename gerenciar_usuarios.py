"""Cadastro local pelo administrador; nunca recebe senhas na linha de comando."""

import argparse
import getpass
import json
import os
from pathlib import Path

from seguranca import carregar_usuarios, gerar_hash, usuario_valido


def salvar_usuario(arquivo: Path, usuario: str, senha: str, atualizar=False) -> None:
    if not usuario_valido(usuario):
        raise ValueError("Use até 64 letras sem acentos, números ou os símbolos _ . @ - no usuário.")
    usuarios = carregar_usuarios(arquivo)
    if usuario in usuarios and not atualizar:
        raise ValueError("Usuário já cadastrado. Use --atualizar para trocar a senha.")
    usuarios[usuario] = gerar_hash(senha)
    gravar_usuarios(arquivo, usuarios)


def gravar_usuarios(arquivo: Path, usuarios: dict) -> None:
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    temporario = arquivo.with_suffix(".tmp")
    with temporario.open("w", encoding="utf-8") as destino:
        json.dump(usuarios, destino, indent=2)
    # Restringe acesso ao proprietário em sistemas que suportam permissões POSIX.
    temporario.chmod(0o600)
    temporario.replace(arquivo)


def main() -> None:
    parser = argparse.ArgumentParser(description="Gerenciar usuários autorizados do dashboard.")
    parser.add_argument("usuario")
    opcoes = parser.add_mutually_exclusive_group()
    opcoes.add_argument("--remover", action="store_true")
    opcoes.add_argument("--atualizar", action="store_true")
    args = parser.parse_args()
    pasta = Path(os.environ.get("DASHBOARD_AUTH_DIR", str(Path(__file__).resolve().parent / ".auth")))
    arquivo = pasta / "usuarios.json"
    try:
        if args.remover:
            usuarios = carregar_usuarios(arquivo)
            if args.usuario not in usuarios:
                raise ValueError("Usuário não encontrado.")
            del usuarios[args.usuario]
            gravar_usuarios(arquivo, usuarios)
        else:
            senha = getpass.getpass("Senha (mínimo 12 caracteres): ")
            confirmacao = getpass.getpass("Repita a senha: ")
            if senha != confirmacao:
                raise ValueError("As senhas não coincidem.")
            salvar_usuario(arquivo, args.usuario, senha, atualizar=args.atualizar)
    except (ValueError, OSError) as erro:
        parser.exit(1, f"{erro}\n")
    print("Lista local atualizada. Se usar secrets ou variável de ambiente, atualize também a configuração do servidor.")


if __name__ == "__main__":
    main()
