import unittest

import pandas as pd
from streamlit.testing.v1 import AppTest


class RepoFake:
    def __init__(self, cliente):
        self.cliente = cliente

    def listar(self):
        return pd.DataFrame([
            {"id": "a", "nome": "2025.xlsx", "importado_por": "u", "importado_em": "2026-09-01", "linhas": 1},
            {"id": "b", "nome": "2026.xlsx", "importado_por": "u", "importado_em": "2026-09-02", "linhas": 1},
        ])

    def carregar(self, ids):
        anos = {"a": "01/08/2025", "b": "01/08/2026"}
        return pd.DataFrame([
            {"Data": anos[item], "Status": "Cancelado", "Criado por": "Pessoa - Matriz"}
            for item in ids
        ]), 0


class HistoricoInterfaceTests(unittest.TestCase):
    def test_arquivos_recem_enviados_sao_a_selecao_inicial(self):
        codigo = '''
import streamlit as st
from unittest.mock import patch
from supabase_acesso import SessaoSupabase
from app import exibir_historico
from test_historico_interface import RepoFake
st.session_state["autenticado"] = True
st.session_state["cliente_supabase"] = object()
st.session_state["arquivos_recem_enviados"] = ["b"]
st.session_state["sessao_supabase"] = SessaoSupabase(
    "matriz", "u", "c", "Clínica Matriz", "access", "refresh"
)
with patch("app.RepositorioHistorico", RepoFake):
    dados = exibir_historico()
st.write(len(dados) if dados is not None else 0)
'''
        at = AppTest.from_string(codigo, default_timeout=30).run()
        self.assertFalse(at.exception)
        escolha = next(item for item in at.radio if item.label == "Quais arquivos analisar?")
        self.assertEqual(escolha.value, "Somente arquivos recém-enviados")
        self.assertEqual(at.markdown[-1].value.strip("`"), "1")

    def test_area_autenticada_usa_apenas_arquivos_da_clinica(self):
        codigo = '''
import streamlit as st
from unittest.mock import patch
from app import exibir_area_autenticada
st.session_state["autenticado"] = True
with patch("app.verificar_sessao_periodicamente"), \
        patch("app.exibir_historico", return_value=None) as historico:
    exibir_area_autenticada()
    st.write("historico=" + str(historico.call_count))
'''
        at = AppTest.from_string(codigo, default_timeout=30).run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.sidebar.radio), 0)
        self.assertEqual(at.markdown[-1].value, "historico=1")

    def test_historico_exibe_clinica_e_mantem_filtros(self):
        codigo = '''
import streamlit as st
from unittest.mock import patch
from supabase_acesso import SessaoSupabase
from app import exibir_historico, limpar_dados, exibir_dashboard
from test_historico_interface import RepoFake
st.session_state["autenticado"] = True
st.session_state["cliente_supabase"] = object()
st.session_state["sessao_supabase"] = SessaoSupabase(
    "matriz", "u", "c", "Clínica Matriz", "access", "refresh"
)
with patch("app.RepositorioHistorico", RepoFake):
    dados = exibir_historico()
if dados is not None:
    dados, _ = limpar_dados(dados)
    exibir_dashboard(dados)
'''
        at = AppTest.from_string(codigo, default_timeout=30).run()
        self.assertFalse(at.exception)
        self.assertTrue(any("Clínica Matriz" in item.value for item in at.caption))
        self.assertEqual(at.metric[0].value, "2")
        at.sidebar.selectbox[0].set_value("2026").run()
        self.assertFalse(at.exception)
        self.assertEqual(at.metric[0].value, "1")
        seletor = next(item for item in at.multiselect if item.label == "Arquivos incluídos na análise")
        seletor.set_value([]).run()
        self.assertFalse(at.exception)
        self.assertEqual(len(at.metric), 0)


if __name__ == "__main__":
    unittest.main()
