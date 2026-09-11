import time
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import app
from supabase_acesso import AcessoNegado, SessaoSupabase


SESSAO = SessaoSupabase(
    usuario="matriz",
    user_id="11111111-1111-1111-1111-111111111111",
    clinica_id="22222222-2222-2222-2222-222222222222",
    clinica_nome="Clínica Matriz",
    access_token="access-token",
    refresh_token="refresh-token",
)


class AcessoTests(unittest.TestCase):
    def setUp(self):
        self.estado = {"login_usuario": "matriz", "login_senha": "senha"}

    def test_login_bem_sucedido_remove_senha_e_inicia_sessao(self):
        with patch.object(app.st, "session_state", self.estado), \
                patch.object(app, "obter_cliente_supabase", return_value=object()), \
                patch.object(app, "entrar", return_value=SESSAO):
            app.autenticar()
        self.assertNotIn("login_senha", self.estado)
        self.assertTrue(self.estado["autenticado"])
        self.assertEqual(self.estado["sessao_supabase"], SESSAO)

    def test_login_invalido_falha_fechado_sem_detalhe_remoto(self):
        with patch.object(app.st, "session_state", self.estado), \
                patch.object(app, "obter_cliente_supabase", side_effect=AcessoNegado("detalhe remoto")):
            app.autenticar()
        self.assertFalse(self.estado["autenticado"])
        self.assertTrue(self.estado["erro_login"])
        self.assertNotIn("login_senha", self.estado)

    def test_sessao_expirada_por_inatividade_e_limpa(self):
        agora = time.time()
        self.estado.update({"autenticado": True, "sessao_supabase": SESSAO,
                            "inicio_sessao": agora - 100, "ultima_atividade": agora - 1900})
        with patch.object(app.st, "session_state", self.estado), \
                patch.object(app.carregar_dados, "clear"):
            self.assertFalse(app.restaurar_sessao(agora=agora))
        self.assertEqual(self.estado, {})

    def test_restauracao_substitui_tokens_renovados(self):
        agora = time.time()
        renovada = SessaoSupabase(**{**SESSAO.__dict__, "access_token": "novo-access",
                                    "refresh_token": "novo-refresh"})
        self.estado.update({"autenticado": True, "sessao_supabase": SESSAO,
                            "inicio_sessao": agora - 100, "ultima_atividade": agora - 10})
        cliente = object()
        with patch.object(app.st, "session_state", self.estado), \
                patch.object(app, "obter_cliente_supabase", return_value=cliente), \
                patch.object(app, "restaurar", return_value=renovada):
            self.assertTrue(app.restaurar_sessao(agora=agora))
        self.assertEqual(self.estado["sessao_supabase"], renovada)
        self.assertIs(self.estado["cliente_supabase"], cliente)

    def test_app_sem_configuracao_nao_exibe_dashboard(self):
        with patch.dict("os.environ", {}, clear=True):
            at = AppTest.from_file("app.py", default_timeout=15).run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.title), 0)
        self.assertEqual(len(at.sidebar.radio), 0)
        self.assertEqual(len(at.text_input), 2)


if __name__ == "__main__":
    unittest.main()
