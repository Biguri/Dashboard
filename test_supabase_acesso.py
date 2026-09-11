import unittest
from types import SimpleNamespace

from supabase_acesso import (
    AcessoNegado,
    ConfiguracaoInvalida,
    carregar_configuracao,
    email_tecnico,
    entrar,
    restaurar,
    sair,
)


USUARIO_ID = "11111111-1111-1111-1111-111111111111"
CLINICA_ID = "22222222-2222-2222-2222-222222222222"


class Resultado:
    def __init__(self, data=None):
        self.data = data


class ConsultaPerfil:
    def __init__(self, perfil):
        self.perfil = perfil

    def select(self, colunas):
        return self

    def eq(self, coluna, valor):
        self.filtro = (coluna, valor)
        return self

    def single(self):
        return self

    def execute(self):
        return Resultado(self.perfil)


class AuthFake:
    def __init__(self, falhar=False):
        self.falhar = falhar
        self.login_recebido = None
        self.sessao_recebida = None
        self.sign_out_chamado = False

    @staticmethod
    def _resposta(access="access-1", refresh="refresh-1"):
        usuario = SimpleNamespace(id=USUARIO_ID)
        sessao = SimpleNamespace(access_token=access, refresh_token=refresh, user=usuario)
        return SimpleNamespace(session=sessao, user=usuario)

    def sign_in_with_password(self, credenciais):
        self.login_recebido = credenciais
        if self.falhar:
            raise RuntimeError("detalhe secreto do servidor")
        return self._resposta()

    def set_session(self, access_token, refresh_token):
        self.sessao_recebida = (access_token, refresh_token)
        if self.falhar:
            raise RuntimeError("token remoto exposto")
        return self._resposta("access-2", "refresh-2")

    def get_user(self):
        if self.falhar:
            raise RuntimeError("jwt interno")
        return SimpleNamespace(user=SimpleNamespace(id=USUARIO_ID))

    def sign_out(self):
        self.sign_out_chamado = True
        if self.falhar:
            raise RuntimeError("erro remoto")


class ClienteFake:
    def __init__(self, perfil=None, falhar=False):
        self.auth = AuthFake(falhar)
        self.perfil = perfil if perfil is not None else {
            "user_id": USUARIO_ID,
            "clinica_id": CLINICA_ID,
            "nome_usuario": "matriz",
            "clinicas": {"nome": "Clínica Matriz"},
        }

    def table(self, nome):
        if nome != "perfis":
            raise AssertionError(nome)
        return ConsultaPerfil(self.perfil)


class SupabaseAcessoTests(unittest.TestCase):
    def test_email_tecnico_normaliza_nome(self):
        self.assertEqual(email_tecnico(" Clinica.Matriz "), "clinica.matriz@login.dashboard.local")

    def test_nome_invalido_e_rejeitado(self):
        for nome in ("", "com espaço", "ácento", "conta@dominio", "a" * 65, None):
            with self.subTest(nome=nome), self.assertRaises(ValueError):
                email_tecnico(nome)

    def test_configuracao_exige_https_e_chave_publicavel(self):
        casos = (
            {},
            {"SUPABASE_URL": "http://projeto.test", "SUPABASE_PUBLISHABLE_KEY": "sb_publishable_x"},
            {"SUPABASE_URL": "https://projeto.supabase.co", "SUPABASE_PUBLISHABLE_KEY": ""},
            {"SUPABASE_URL": "https://projeto.supabase.co", "SUPABASE_PUBLISHABLE_KEY": "sb_secret_x"},
        )
        for config in casos:
            with self.subTest(config=config), self.assertRaises(ConfiguracaoInvalida):
                carregar_configuracao(config)

    def test_login_valida_identidade_e_perfil(self):
        cliente = ClienteFake()
        sessao = entrar(cliente, "Matriz", "senha")
        self.assertEqual(sessao.usuario, "matriz")
        self.assertEqual(sessao.user_id, USUARIO_ID)
        self.assertEqual(sessao.clinica_id, CLINICA_ID)
        self.assertEqual(sessao.clinica_nome, "Clínica Matriz")
        self.assertEqual(cliente.auth.login_recebido, {
            "email": "matriz@login.dashboard.local", "password": "senha"
        })

    def test_login_sem_perfil_falha_fechado(self):
        cliente = ClienteFake(perfil={})
        with self.assertRaises(AcessoNegado):
            entrar(cliente, "matriz", "senha")
        self.assertTrue(cliente.auth.sign_out_chamado)

    def test_login_nao_repassa_detalhe_do_sdk(self):
        cliente = ClienteFake(falhar=True)
        with self.assertRaises(AcessoNegado) as contexto:
            entrar(cliente, "matriz", "senha")
        self.assertNotIn("servidor", str(contexto.exception))

    def test_restauracao_atualiza_tokens(self):
        cliente = ClienteFake()
        sessao = restaurar(cliente, "matriz", "access-1", "refresh-1")
        self.assertEqual(cliente.auth.sessao_recebida, ("access-1", "refresh-1"))
        self.assertEqual((sessao.access_token, sessao.refresh_token), ("access-2", "refresh-2"))

    def test_logout_nao_propaga_erro_remoto(self):
        cliente = ClienteFake(falhar=True)
        sair(cliente)
        self.assertTrue(cliente.auth.sign_out_chamado)


if __name__ == "__main__":
    unittest.main()
