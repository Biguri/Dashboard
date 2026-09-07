import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd
from sqlalchemy import create_engine
from streamlit.testing.v1 import AppTest

from historico import RepositorioHistorico, preparar_arquivo
from seguranca import gerar_hash


class HistoricoInterfaceTests(unittest.TestCase):
    def test_historico_compartilhado_com_filtro_anual_e_selecao_vazia(self):
        with TemporaryDirectory() as tmp:
            url = 'sqlite:///' + (Path(tmp) / 'historico.db').as_posix()
            engine = create_engine(url)
            repo = RepositorioHistorico(engine)
            for ano in (2025, 2026):
                df = pd.DataFrame({'Data': [f'01/08/{ano}'], 'Status': ['Cancelado'],
                                   'Criado por': ['Pessoa - Matriz']})
                repo.importar([preparar_arquivo(f'{ano}.xlsx', str(ano).encode(), df)], 'teste')
            engine.dispose()
            hash_senha = gerar_hash('Senha de teste 123!')
            codigo = f'''
import streamlit as st
from unittest.mock import patch
from sqlalchemy import create_engine
from historico import RepositorioHistorico
from seguranca import iniciar_sessao
from app import exibir_historico, limpar_dados, exibir_dashboard
if not st.session_state.get('autenticado'):
    iniciar_sessao(st.session_state, 'teste', {hash_senha!r})
engine = create_engine({url!r})
try:
    with patch('app.conectar_historico', return_value=RepositorioHistorico(engine)):
        dados = exibir_historico()
    if dados is not None:
        dados, _ = limpar_dados(dados)
        exibir_dashboard(dados)
finally:
    engine.dispose()
'''
            with patch.dict(os.environ, {'DATABASE_URL': 'postgresql://configurado/teste',
                                         'DASHBOARD_USERS_JSON': json.dumps({'teste': hash_senha})}):
                at = AppTest.from_string(codigo, default_timeout=30).run()
                self.assertFalse(at.exception)
                self.assertEqual(at.metric[0].value, '2')
                at.sidebar.selectbox[0].set_value('2026').run()
                self.assertFalse(at.exception)
                self.assertEqual(at.metric[0].value, '1')
                seletor = next(m for m in at.multiselect if m.label == 'Arquivos incluídos na análise')
                seletor.set_value([]).run()
                self.assertFalse(at.exception)
                self.assertEqual(len(at.metric), 0)


if __name__ == '__main__':
    unittest.main()
