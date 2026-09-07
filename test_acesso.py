import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
from seguranca import gerar_hash


class AcessoInterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.senha = 'Acesso teste privado 123!'
        cls.hash = gerar_hash(cls.senha)

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = patch.dict(os.environ, {
            'DASHBOARD_USERS_JSON': json.dumps({'autorizado': self.hash}),
            'DASHBOARD_AUTH_DIR': self.tmp.name,
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.at = AppTest.from_file('app.py', default_timeout=15).run()

    def entrar(self, usuario='autorizado', senha=None):
        self.at.text_input(key='login_usuario').input(usuario)
        self.at.text_input(key='login_senha').input(self.senha if senha is None else senha)
        self.at.button[0].click().run()
        self.assertFalse(self.at.exception)

    def assert_bloqueado(self, at=None):
        at = self.at if at is None else at
        self.assertFalse(at.exception)
        self.assertEqual(len(at.title), 0)
        self.assertEqual(len(at.metric), 0)
        self.assertEqual(len(at.dataframe), 0)
        self.assertEqual(len(at.sidebar.radio), 0)
        self.assertEqual(len(at.get('file_uploader')), 0)

    def test_anonimo_nao_recebe_dashboard_nem_upload(self):
        self.assert_bloqueado()

    def test_senha_de_exemplo_nao_abre_dashboard(self):
        self.entrar('admin', 'Dashboard@123')
        self.assert_bloqueado()

    def test_usuario_nao_listado_negado(self):
        self.entrar('intruso')
        self.assert_bloqueado()

    def test_senha_errada_nao_revela_se_usuario_existe(self):
        self.entrar(senha='errada')
        mensagem = self.at.error[0].value
        self.entrar('intruso', 'errada')
        self.assertEqual(self.at.error[0].value, mensagem)
        self.assert_bloqueado()

    def test_autorizado_entra_e_senha_e_removida_da_sessao(self):
        self.entrar()
        self.assertEqual(len(self.at.title), 1)
        self.assertEqual(len(self.at.sidebar.radio), 1)
        self.assertNotIn('login_senha', self.at.session_state)

    def test_logout_remove_dados_e_acesso(self):
        self.entrar()
        self.assertEqual(len(self.at.title), 1)
        self.at.session_state['dados_privados'] = 'conteudo'
        self.at.sidebar.button[0].click().run()
        self.assert_bloqueado()
        self.assertNotIn('dados_privados', self.at.session_state)

    def test_sessoes_independentes(self):
        self.entrar()
        self.assertEqual(len(self.at.title), 1)
        outra = AppTest.from_file('app.py').run()
        self.assert_bloqueado(outra)

    def test_query_string_nao_concede_acesso(self):
        self.at.query_params['autenticado'] = 'true'
        self.at.query_params['usuario'] = 'autorizado'
        self.at.run()
        self.assert_bloqueado()

    def test_revogacao_fecha_sessao_existente(self):
        self.entrar()
        self.assertEqual(len(self.at.title), 1)
        os.environ['DASHBOARD_USERS_JSON'] = '{}'
        self.at.run()
        self.assert_bloqueado()

    def test_expiracao_fecha_sessao_existente(self):
        self.entrar()
        self.assertEqual(len(self.at.title), 1)
        self.at.session_state['ultima_atividade'] = time.time() - 1900
        self.at.run()
        self.assert_bloqueado()

    def test_configuracao_quebrada_falha_fechada(self):
        os.environ['DASHBOARD_USERS_JSON'] = '{invalido'
        self.at.run()
        self.entrar()
        self.assert_bloqueado()

    def test_bloqueio_nao_e_contornado_por_nova_sessao(self):
        for _ in range(5):
            self.entrar(senha='errada')
        self.at = AppTest.from_file('app.py').run()
        self.entrar()
        self.assert_bloqueado()


if __name__ == '__main__':
    unittest.main()
