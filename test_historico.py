import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.exc import StatementError

from historico import (RepositorioHistorico, preparar_arquivo, consolidar_arquivos,
                       criar_engine_postgres)


class HistoricoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.url = 'sqlite:///' + (Path(self.tmp.name) / 'historico.db').as_posix()
        self.engine = create_engine(self.url)
        self.addCleanup(self.engine.dispose)
        self.repo = RepositorioHistorico(self.engine)
        self.df = pd.DataFrame({'Data': ['01/08/2026'], 'Status': ['Cancelado'], 'ID paciente': [1]})

    def arquivo(self, nome='agosto.xlsx', conteudo=b'arquivo-a', df=None):
        return preparar_arquivo(nome, conteudo, self.df if df is None else df)

    def test_importacao_persiste_apos_reabrir_conexao(self):
        resultado = self.repo.importar([self.arquivo()], 'operador')
        self.assertEqual(resultado, {'novos': 1, 'repetidos': 0})
        outro_engine = create_engine(self.url)
        try:
            outro = RepositorioHistorico(outro_engine)
            lista = outro.listar()
            self.assertEqual(lista['usuario'].tolist(), ['operador'])
            carregado, removidas = outro.carregar(lista['id'].tolist())
            pd.testing.assert_frame_equal(carregado, self.df)
            self.assertEqual(removidas, 0)
        finally:
            outro_engine.dispose()

    def test_mesmo_conteudo_com_outro_nome_nao_e_importado_duas_vezes(self):
        resultado = self.repo.importar([self.arquivo(), self.arquivo('copia.xlsx')], 'operador')
        self.assertEqual(resultado, {'novos': 1, 'repetidos': 1})
        self.assertEqual(len(self.repo.listar()), 1)

    def test_varios_meses_e_selecao_de_arquivos(self):
        setembro = self.df.assign(Data='01/09/2026')
        self.repo.importar([self.arquivo(), self.arquivo('setembro.xlsx', b'arquivo-b', setembro)], 'operador')
        lista = self.repo.listar()
        todos, _ = self.repo.carregar(lista['id'].tolist())
        self.assertEqual(len(todos), 2)
        escolhido, _ = self.repo.carregar([lista['id'].iloc[0]])
        self.assertEqual(len(escolhido), 1)
        vazio, _ = self.repo.carregar([])
        self.assertTrue(vazio.empty)

    def test_duplicatas_exatas_entre_arquivos_preservam_repeticoes_internas(self):
        duplo = pd.concat([self.df, self.df], ignore_index=True)
        resultado, removidas = consolidar_arquivos([('a', duplo), ('b', self.df)])
        self.assertEqual(len(resultado), 2)
        self.assertEqual(removidas, 1)

    def test_status_diferente_nao_e_descartado(self):
        resultado, removidas = consolidar_arquivos([
            ('a', self.df), ('b', self.df.assign(Status='Atendido')),
        ])
        self.assertEqual(len(resultado), 2)
        self.assertEqual(removidas, 0)

    def test_arquivo_sem_data_ou_status_rejeitado(self):
        with self.assertRaises(ValueError):
            self.arquivo(df=pd.DataFrame({'Outra': [1]}))

    def test_lote_invalido_nao_grava_parcialmente(self):
        arquivo = self.arquivo('outro.xlsx', b'outro')
        arquivo['dados'] = {'invalido': object()}
        with self.assertRaises(StatementError):
            self.repo.importar([self.arquivo(), arquivo], 'operador')
        self.assertTrue(self.repo.listar().empty)

    def test_producao_rejeita_sqlite(self):
        with self.assertRaises(ValueError):
            criar_engine_postgres('sqlite:///nao_permitido.db')

    def test_conexao_postgres_exige_tls(self):
        with self.assertRaises(ValueError):
            criar_engine_postgres('postgresql://usuario:senha@servidor/banco?sslmode=disable')
        engine = criar_engine_postgres('postgresql://usuario:senha@servidor/banco')
        try:
            self.assertEqual(engine.url.drivername, 'postgresql+psycopg')
            self.assertEqual(engine.url.query['sslmode'], 'require')
            self.assertTrue(engine.hide_parameters)
        finally:
            engine.dispose()


if __name__ == '__main__':
    unittest.main()
