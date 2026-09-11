import base64
import unittest

import pandas as pd

from historico import ErroHistorico, RepositorioHistorico, consolidar_arquivos, preparar_arquivo


class Resposta:
    def __init__(self, data):
        self.data = data


class ConsultaFake:
    def __init__(self, cliente):
        self.cliente = cliente
        self.colunas = None
        self.ids = None

    def select(self, colunas):
        self.colunas = colunas
        return self

    def in_(self, coluna, valores):
        self.ids = list(valores)
        return self

    def order(self, coluna):
        return self

    def execute(self):
        if self.cliente.erro:
            raise RuntimeError("resposta confidencial do servidor")
        if self.colunas == "id,dados":
            dados = self.cliente.dados
            if self.ids is not None:
                dados = [linha for linha in dados if linha["id"] in self.ids]
            return Resposta(dados)
        return Resposta(self.cliente.lista)


class RpcFake:
    def __init__(self, cliente):
        self.cliente = cliente

    def execute(self):
        if self.cliente.erro:
            raise RuntimeError("resposta confidencial do servidor")
        return Resposta(self.cliente.resultado_rpc)


class ClienteFake:
    def __init__(self):
        self.erro = False
        self.resultado_rpc = {"novos": 1, "repetidos": 0}
        self.lista = []
        self.dados = []
        self.chamadas_rpc = []

    def rpc(self, nome, parametros):
        self.chamadas_rpc.append((nome, parametros))
        return RpcFake(self)

    def table(self, nome):
        if nome != "dashboard_arquivos":
            raise AssertionError(nome)
        return ConsultaFake(self)


class HistoricoTests(unittest.TestCase):
    def setUp(self):
        self.cliente = ClienteFake()
        self.repo = RepositorioHistorico(self.cliente)
        self.df = pd.DataFrame({"Data": ["01/08/2026"], "Status": ["Cancelado"], "ID paciente": [1]})

    def arquivo(self, nome="agosto.xlsx", conteudo=b"arquivo-a", df=None):
        return preparar_arquivo(nome, conteudo, self.df if df is None else df)

    def test_importacao_nao_aceita_identidade_controlada_pelo_cliente(self):
        resultado = self.repo.importar([self.arquivo()])
        self.assertEqual(resultado, {"novos": 1, "repetidos": 0})
        nome_rpc, parametros = self.cliente.chamadas_rpc[0]
        self.assertEqual(nome_rpc, "importar_dashboard_arquivos")
        payload = parametros["p_arquivos"][0]
        self.assertNotIn("clinica_id", payload)
        self.assertNotIn("importado_por", payload)
        self.assertEqual(base64.b64decode(payload["conteudo_base64"]), b"arquivo-a")

    def test_lote_acima_de_vinte_e_rejeitado_antes_da_rede(self):
        with self.assertRaises(ValueError):
            self.repo.importar([self.arquivo(str(i) + ".xlsx", str(i).encode()) for i in range(21)])
        self.assertEqual(self.cliente.chamadas_rpc, [])

    def test_resposta_rpc_malformada_e_rejeitada(self):
        self.cliente.resultado_rpc = {"novos": "1", "repetidos": 0}
        with self.assertRaises(ErroHistorico):
            self.repo.importar([self.arquivo()])

    def test_listagem_preserva_metadados(self):
        self.cliente.lista = [{"id": "a" * 64, "nome": "agosto.xlsx", "importado_por": "u",
                               "importado_em": "2026-09-11T10:00:00Z", "linhas": 1}]
        lista = self.repo.listar()
        self.assertEqual(lista["nome"].tolist(), ["agosto.xlsx"])

    def test_carregamento_seleciona_ids_e_consolida(self):
        setembro = self.df.assign(Data="01/09/2026")
        self.cliente.dados = [
            {"id": "a", "dados": self.df.to_dict(orient="records")},
            {"id": "b", "dados": setembro.to_dict(orient="records")},
        ]
        carregado, removidas = self.repo.carregar(["b"])
        self.assertEqual(carregado["Data"].tolist(), ["01/09/2026"])
        self.assertEqual(removidas, 0)

    def test_lista_vazia_nao_consulta_rede(self):
        carregado, removidas = self.repo.carregar([])
        self.assertTrue(carregado.empty)
        self.assertEqual(removidas, 0)

    def test_erro_remoto_e_sanitizado(self):
        self.cliente.erro = True
        with self.assertRaises(ErroHistorico) as contexto:
            self.repo.listar()
        self.assertNotIn("confidencial", str(contexto.exception))

    def test_duplicatas_exatas_preservam_repeticoes_internas(self):
        duplo = pd.concat([self.df, self.df], ignore_index=True)
        resultado, removidas = consolidar_arquivos([("a", duplo), ("b", self.df)])
        self.assertEqual(len(resultado), 2)
        self.assertEqual(removidas, 1)

    def test_status_diferente_nao_e_descartado(self):
        resultado, removidas = consolidar_arquivos([
            ("a", self.df), ("b", self.df.assign(Status="Atendido")),
        ])
        self.assertEqual(len(resultado), 2)
        self.assertEqual(removidas, 0)

    def test_arquivo_sem_data_ou_status_rejeitado(self):
        with self.assertRaises(ValueError):
            self.arquivo(df=pd.DataFrame({"Outra": [1]}))


if __name__ == "__main__":
    unittest.main()
