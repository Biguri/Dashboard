from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile, BadZipFile

import pandas as pd
from streamlit.testing.v1 import AppTest

from app import carregar_dados


class LeituraTests(unittest.TestCase):
    def test_cache_de_leitura_nao_e_compartilhado_entre_sessoes(self):
        with TemporaryDirectory() as tmp:
            arquivo = Path(tmp) / 'dados.xlsx'
            pd.DataFrame({'valor': [10]}).to_excel(arquivo, index=False)
            codigo = (
                'import streamlit as st\nfrom app import carregar_dados\n'
                # AppTest usa o mesmo session_id fixo em todas as instâncias.
                # IDs distintos simulam as sessões reais do servidor.
                'from streamlit.runtime.scriptrunner import get_script_run_ctx\n'
                'get_script_run_ctx().session_id = ID_SESSAO\n'
                f'st.dataframe(carregar_dados({str(arquivo)!r}))\n'
            )
            primeira = AppTest.from_string(codigo.replace('ID_SESSAO', repr(tmp + '-primeira'))).run()
            self.assertFalse(primeira.exception)
            self.assertEqual(primeira.dataframe[0].value['valor'].iloc[0], 10)
            pd.DataFrame({'valor': [20]}).to_excel(arquivo, index=False)
            segunda = AppTest.from_string(codigo.replace('ID_SESSAO', repr(tmp + '-segunda'))).run()
            self.assertFalse(segunda.exception)
            self.assertEqual(segunda.dataframe[0].value['valor'].iloc[0], 20)
            primeira.run()
            self.assertEqual(primeira.dataframe[0].value['valor'].iloc[0], 10)

    def planilha(self):
        buffer = BytesIO()
        esperado = pd.DataFrame({'valor': [12.5], 'data': [pd.Timestamp('2026-08-01')]})
        esperado.to_excel(buffer, index=False, engine='openpyxl')
        buffer.seek(0)
        return buffer, esperado

    def test_leitura_preserva_numeros_e_datas(self):
        buffer, esperado = self.planilha()
        pd.testing.assert_frame_equal(carregar_dados.__wrapped__(buffer), esperado)

    def test_fallback_para_estilo_invalido_preserva_dados(self):
        original, esperado = self.planilha()
        alterado = BytesIO()
        with ZipFile(original) as entrada, ZipFile(alterado, 'w') as saida:
            for item in entrada.infolist():
                conteudo = entrada.read(item.filename)
                if item.filename == 'xl/styles.xml':
                    conteudo = conteudo.replace(b'<sz val="11"', b'<sz val="invalido"')
                saida.writestr(item, conteudo)
        alterado.seek(0)
        pd.testing.assert_frame_equal(carregar_dados.__wrapped__(alterado), esperado)

    def test_arquivo_invalido_nao_produz_dados(self):
        with self.assertRaises(BadZipFile):
            carregar_dados.__wrapped__(BytesIO(b'arquivo invalido'))

if __name__ == '__main__':
    unittest.main()
