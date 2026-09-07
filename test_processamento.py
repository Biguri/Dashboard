import unittest

import pandas as pd

from app import (analisar_cancelamentos, calcular_kpis, identificar_clinica,
                 limpar_dados, filtrar_dados, preparar_evolucao_diaria, criar_ranking_cancelamentos,
                 preparar_evolucao_mensal, resumir_movimentacao)


class ProcessamentoTests(unittest.TestCase):
    def test_movimentacao_reconcilia_status_e_grupos(self):
        df = pd.DataFrame({
            'Status': ['Atendido', 'Faltou', 'Faltou (com aviso prévio)', 'Cancelado',
                       'Cancelado pelo profissional', 'Agendado', 'Não atendido', None],
            'Profissional': ['A', 'A', 'B', 'B', 'A', 'A', None, 'B'],
        })
        resumo = resumir_movimentacao(df).iloc[0]
        self.assertEqual(resumo.to_dict(), {'Total': 8, 'Atendidos': 1, 'Faltas': 2,
                         'Cancelamentos': 2, 'Agendados': 1, 'Outros': 2})
        grupos = resumir_movimentacao(df, 'Profissional')
        self.assertEqual(grupos['Total'].sum(), 8)
        self.assertTrue((grupos.drop(columns=['Profissional', 'Total']).sum(axis=1) == grupos['Total']).all())

    def test_movimentacao_sem_status_nao_inventa_zero(self):
        resumo = resumir_movimentacao(pd.DataFrame({'Paciente': ['Ana']})).iloc[0]
        self.assertEqual(resumo['Total'], 1)
        self.assertTrue(pd.isna(resumo['Atendidos']))
        vazio = resumir_movimentacao(pd.DataFrame({'Status': []})).iloc[0]
        self.assertEqual(vazio['Total'], 0)
        self.assertEqual(vazio['Faltas'], 0)

    def test_filtro_anual_e_resumo_mensal(self):
        df = pd.DataFrame({
            'data': pd.to_datetime(['2025-08-01', '2026-08-01', '2026-09-01']),
            'Status': ['Cancelado', 'Cancelado', 'Cancelado pelo profissional'],
        })
        filtrado = filtrar_dados(df, anos=['2026'])
        self.assertEqual(len(filtrado), 2)
        mensal = preparar_evolucao_mensal(filtrado)
        self.assertEqual(mensal.index.tolist(), ['2026-08', '2026-09'])
        self.assertEqual(mensal['Paciente'].sum(), 1)
        self.assertEqual(mensal['Profissional'].sum(), 1)

    def test_data_iso_restaurada_do_banco_preserva_mes_e_dia(self):
        df, _ = limpar_dados(pd.DataFrame({'Data': ['2026-08-03T00:00:00']}))
        self.assertEqual(df['data'].iloc[0], pd.Timestamp('2026-08-03'))

    def test_ranking_separa_responsavel_e_pacientes_homonimos(self):
        df = pd.DataFrame({
            'Status': ['Cancelado', 'Cancelado', 'Cancelado pelo profissional', 'Faltou', 'Atendido'],
            'ID paciente': [1, 2, 1, 1, 1],
            'Paciente': ['Ana', 'Ana', 'Ana', 'Ana', 'Ana'],
            'Profissional': ['Prof A', 'Prof A', 'Prof B', 'Prof A', 'Prof A'],
        })
        pacientes = criar_ranking_cancelamentos(df, 'paciente')
        profissionais = criar_ranking_cancelamentos(df, 'profissional')
        self.assertEqual(len(pacientes), 2)
        self.assertEqual(pacientes['Cancelamentos'].sum(), 2)
        self.assertEqual(profissionais['Profissional'].tolist(), ['Prof B'])
        self.assertEqual(profissionais['Cancelamentos'].sum(), 1)
        self.assertTrue(criar_ranking_cancelamentos(df.iloc[:0], 'paciente').empty)

    def test_ranking_sem_id_agrupa_por_nome_e_mantem_ausentes(self):
        df = pd.DataFrame({'Status': ['Cancelado'] * 3, 'Paciente': ['Ana ', 'Ana', None]})
        ranking = criar_ranking_cancelamentos(df, 'paciente')
        self.assertEqual(ranking['Cancelamentos'].tolist(), [2, 1])
        self.assertEqual(ranking['Cancelamentos'].sum(), 3)

    def test_filtros_combinados_e_selecao_vazia(self):
        df = pd.DataFrame({'data': pd.to_datetime(['2026-08-01', '2026-09-01', None]),
                           'clinica': ['Matriz', 'Ivinhema', 'Matriz']})
        self.assertEqual(len(filtrar_dados(df, ['2026-08'], ['Matriz'])), 1)
        self.assertTrue(filtrar_dados(df, [], ['Matriz']).empty)
        self.assertEqual(len(filtrar_dados(df, ['Sem data'], ['Matriz'])), 1)
        self.assertEqual(len(filtrar_dados(df)), 3)

    def test_evolucao_exclui_datas_ausentes_e_preenche_dias(self):
        df = pd.DataFrame({
            'data': pd.to_datetime(['2026-08-01', '2026-08-03', None]),
            'Status': ['Cancelado', 'Cancelado pelo profissional', 'Cancelado'],
        })
        diario = preparar_evolucao_diaria(df)
        self.assertEqual(len(diario), 3)
        self.assertEqual(diario['Paciente'].sum(), 1)
        self.assertEqual(diario['Profissional'].sum(), 1)
        self.assertEqual(diario.iloc[1].sum(), 0)
        self.assertTrue(preparar_evolucao_diaria(df.iloc[:0]).empty)

    def test_identificacao_de_unidade_no_criador(self):
        self.assertEqual(identificar_clinica('Pessoa - Ivinhema'), 'Ivinhema')
        self.assertEqual(identificar_clinica('Clinica Ivinhema'), 'Ivinhema')
        self.assertEqual(identificar_clinica('Recepção Fisiovida Matriz'), 'Matriz')
        self.assertEqual(identificar_clinica('Pessoa - FISIOTERAPIA'), 'Fisioterapia')
        self.assertEqual(identificar_clinica(None), 'Clínica não informada')
        self.assertEqual(identificar_clinica('Pessoa sem unidade'), 'Clínica não informada')
        self.assertEqual(identificar_clinica('Pessoa - Matriz e Neurointensivo'),
                         'Múltiplas clínicas: Matriz / Neurointensivo')

    def test_integracao_criado_por_sem_duplicar_multiplas_unidades(self):
        origem = pd.DataFrame({
            'Status': ['Cancelado', 'Cancelado pelo profissional', 'Cancelado', 'Atendido'],
            'Criado por': ['Pessoa - Ivinhema', 'Pessoa - Matriz e Neurointensivo',
                          'Sem unidade', 'Pessoa - Matriz'],
        })
        limpo, avisos = limpar_dados(origem)
        resumo, tabela = analisar_cancelamentos(limpo, 'clinica', True)
        self.assertEqual(resumo['paciente'], 2)
        self.assertEqual(resumo['profissional'], 1)
        self.assertEqual(tabela['Total de registros'].sum(), 4)
        self.assertEqual(tabela['Cancelamentos'].sum(), 3)
        self.assertTrue(any('múltiplas' in aviso for aviso in avisos))

    def test_cancelamento_generico_nao_e_atribuido_ao_paciente(self):
        dados = pd.DataFrame({'Status': [
            'Cancelado', ' Cancelado pelo profissional ',
            'Cancelado pelo paciente', 'Faltou', 'Atendido', None,
        ]})
        resumo, por_clinica = analisar_cancelamentos(dados)
        self.assertEqual(resumo['total_cancelamentos'], 3)
        self.assertEqual(resumo['paciente'], 1)
        self.assertEqual(resumo['profissional'], 1)
        self.assertEqual(resumo['nao_informado'], 1)
        self.assertIsNone(por_clinica)

    def test_regra_confirmada_e_todas_as_clinicas(self):
        dados = pd.DataFrame({
            'Status': ['Cancelado', 'Cancelado pelo profissional', 'Atendido', 'Cancelado'],
            'Unidade': ['A', 'B', 'C', None],
        })
        resumo, tabela = analisar_cancelamentos(dados, 'Unidade', True)
        self.assertEqual(resumo['paciente'], 2)
        self.assertEqual(tabela['Total de registros'].sum(), 4)
        self.assertEqual(tabela['Cancelamentos'].sum(), 3)
        por_unidade = tabela.set_index('Clínica')
        self.assertEqual(por_unidade.loc['C', 'Cancelamentos'], 0)
        self.assertEqual(por_unidade.loc['Clínica não informada', 'Paciente'], 1)

    def test_ausencia_de_status_nao_significa_zero_cancelamentos(self):
        resumo, tabela = analisar_cancelamentos(pd.DataFrame({'Cidade': ['A']}))
        self.assertIsNone(resumo['total_cancelamentos'])
        self.assertIsNone(tabela)

    def test_limpeza_preserva_origem_e_registros_parciais(self):
        origem = pd.DataFrame({
            'valor': ['R$ 1.234,56', 100.0, 'invalido', ' '],
            'data': ['31/12/2026', '01/02/2026', 'invalida', None],
            'categoria': [' Consulta ', 'Consulta', None, None],
        })
        copia = origem.copy(deep=True)
        limpo, avisos = limpar_dados(origem)
        pd.testing.assert_frame_equal(origem, copia)
        self.assertEqual(len(limpo), 3)
        self.assertAlmostEqual(limpo['valor'].iloc[0], 1234.56)
        self.assertEqual(limpo['data'].iloc[1], pd.Timestamp('2026-02-01'))
        self.assertTrue(pd.isna(limpo['data'].iloc[2]))
        self.assertTrue(avisos)
        kpis = calcular_kpis(limpo)
        self.assertEqual(kpis['total_registros'], 3)
        self.assertAlmostEqual(kpis['faturamento_total'], 1334.56)
        self.assertAlmostEqual(kpis['ticket_medio'], 667.28)
        self.assertEqual(kpis['total_categorias'], 1)

    def test_colunas_ausentes_nao_inventam_valores(self):
        limpo, avisos = limpar_dados(pd.DataFrame({'nome': ['Ana']}))
        kpis = calcular_kpis(limpo)
        self.assertEqual(kpis['total_registros'], 1)
        self.assertIsNone(kpis['faturamento_total'])
        self.assertIsNone(kpis['ticket_medio'])
        self.assertIsNone(kpis['total_categorias'])
        self.assertTrue(avisos)

    def test_base_vazia(self):
        limpo, _ = limpar_dados(pd.DataFrame())
        self.assertEqual(calcular_kpis(limpo)['total_registros'], 0)

    def test_numeros_e_datas_excel(self):
        origem = pd.DataFrame({'valor': [0, -25, 10.5], 'data': [45292, 45293, None]})
        limpo, _ = limpar_dados(origem)
        self.assertEqual(limpo['data'].iloc[0], pd.Timestamp('2024-01-01'))
        self.assertAlmostEqual(calcular_kpis(limpo)['faturamento_total'], -14.5)


if __name__ == '__main__':
    unittest.main()
