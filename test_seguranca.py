import json
import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import seguranca as seg
from gerenciar_usuarios import salvar_usuario


class SegurancaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.senha = 'Senha de teste longa 123!'
        cls.hash = seg.gerar_hash(cls.senha)

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pasta = Path(self.tmp.name)
        self.usuarios = {'autorizado': self.hash}

    def test_senha_correta(self):
        self.assertTrue(seg.verificar_senha(self.senha, self.hash))

    def test_senha_errada_e_vazia(self):
        for senha in ['errada', '', 'Dashboard@123', 'x' * 1025]:
            with self.subTest(senha_len=len(senha)):
                self.assertFalse(seg.verificar_senha(senha, self.hash))

    def test_salt_aleatorio_sem_senha_em_texto(self):
        outro = seg.gerar_hash(self.senha)
        self.assertNotEqual(outro, self.hash)
        self.assertNotIn(self.senha, outro)
        self.assertTrue(seg.verificar_senha(self.senha, outro))

    def test_hash_malformado_negado(self):
        for hash in ['texto', '', None, 'pbkdf2_sha256$999999999999$aa$aa']:
            with self.subTest(hash=hash):
                self.assertFalse(seg.verificar_senha(self.senha, hash))

    def test_cadastro_rejeita_senha_curta(self):
        with self.assertRaises(ValueError):
            seg.gerar_hash('123')

    def test_sem_configuracao_nao_existe_admin_padrao(self):
        self.assertEqual(seg.carregar_usuarios(self.pasta / 'ausente.json'), {})

    def test_configuracao_aceita_somente_hash_valido(self):
        arquivo = self.pasta / 'usuarios.json'
        arquivo.write_text(json.dumps(self.usuarios), encoding='utf-8')
        self.assertEqual(seg.carregar_usuarios(arquivo), self.usuarios)
        for config in ['{', '[]', '{"admin": "Dashboard@123"}', '{"": "invalido"}']:
            with self.subTest(config=config), self.assertRaises(ValueError):
                seg.carregar_usuarios(arquivo, config)

    def test_configuracao_externa_substitui_lista_local(self):
        arquivo = self.pasta / 'usuarios.json'
        arquivo.write_text(json.dumps(self.usuarios), encoding='utf-8')
        self.assertEqual(seg.carregar_usuarios(arquivo, '{}'), {})

    def test_cadastro_grava_hash_e_preserva_outros_usuarios(self):
        arquivo = self.pasta / 'usuarios.json'
        salvar_usuario(arquivo, 'primeiro', self.senha)
        salvar_usuario(arquivo, 'segundo', self.senha)
        usuarios = seg.carregar_usuarios(arquivo)
        self.assertEqual(set(usuarios), {'primeiro', 'segundo'})
        self.assertNotIn(self.senha, arquivo.read_text())
        self.assertTrue(seg.verificar_senha(self.senha, usuarios['primeiro']))
        with self.assertRaises(ValueError):
            salvar_usuario(arquivo, 'primeiro', self.senha)

    def test_atualizacao_de_senha_requer_opcao_explicita(self):
        arquivo = self.pasta / 'usuarios.json'
        salvar_usuario(arquivo, 'primeiro', self.senha)
        salvar_usuario(arquivo, 'primeiro', 'Nova senha longa 456!', atualizar=True)
        hash = seg.carregar_usuarios(arquivo)['primeiro']
        self.assertFalse(seg.verificar_senha(self.senha, hash))
        self.assertTrue(seg.verificar_senha('Nova senha longa 456!', hash))

    def test_banco_indisponivel_nao_pode_autorizar(self):
        with self.assertRaises(sqlite3.OperationalError):
            seg.validar_login('autorizado', self.senha, self.usuarios, self.pasta, agora=100)

    def test_sessao_com_timestamp_invalido_e_negada(self):
        for valor in [None, '100', float('nan'), float('inf'), 99999]:
            estado = {}
            seg.iniciar_sessao(estado, 'autorizado', self.hash, agora=100)
            estado['ultima_atividade'] = valor
            self.assertFalse(seg.sessao_autorizada(estado, self.usuarios, agora=101))

    def test_verificacao_periodica_nao_renova_atividade(self):
        estado = {}
        seg.iniciar_sessao(estado, 'autorizado', self.hash, agora=100)
        self.assertTrue(seg.sessao_autorizada(estado, self.usuarios, agora=200, atualizar=False))
        self.assertEqual(estado['ultima_atividade'], 100)
        self.assertFalse(seg.sessao_autorizada(estado, self.usuarios, agora=1900, atualizar=False))

    def test_login_somente_usuario_autorizado(self):
        db = self.pasta / 'tentativas.db'
        self.assertTrue(seg.validar_login('autorizado', self.senha, self.usuarios, db, agora=100))
        self.assertFalse(seg.validar_login('intruso', self.senha, self.usuarios, db, agora=100))
        self.assertFalse(seg.validar_login('admin', 'Dashboard@123', self.usuarios, db, agora=100))

    def test_bloqueio_persiste_entre_chamadas_e_expira(self):
        db = self.pasta / 'tentativas.db'
        for agora in [100, 200, 300, 400, 999]:
            self.assertFalse(seg.validar_login('autorizado', 'errada', self.usuarios, db, agora=agora))
        self.assertFalse(seg.validar_login('autorizado', self.senha, self.usuarios, db, agora=1000))
        self.assertFalse(seg.validar_login('autorizado', self.senha, self.usuarios, db, agora=1298))
        self.assertTrue(seg.validar_login('autorizado', self.senha, self.usuarios, db, agora=1299))

    def test_quatro_falhas_permitem_login_correto_na_quinta_tentativa(self):
        db = self.pasta / 'tentativas.db'
        for agora in range(100, 104):
            self.assertFalse(seg.validar_login('autorizado', 'errada', self.usuarios, db, agora=agora))
        self.assertTrue(seg.validar_login('autorizado', self.senha, self.usuarios, db, agora=104))

    def test_fim_do_bloqueio_reinicia_as_cinco_tentativas(self):
        db = self.pasta / 'tentativas.db'
        for _ in range(5):
            self.assertFalse(seg.validar_login('autorizado', 'errada', self.usuarios, db, agora=100))
        for agora in range(400, 404):
            self.assertFalse(seg.validar_login('autorizado', 'errada', self.usuarios, db, agora=agora))
        self.assertTrue(seg.validar_login('autorizado', self.senha, self.usuarios, db, agora=404))

    def test_entrada_maliciosa_nao_altera_lista(self):
        for usuario in ["' OR 1=1 --", '../admin', '<script>alert(1)</script>', 'x' * 1000]:
            self.assertFalse(seg.validar_login(usuario, self.senha, self.usuarios,
                                              self.pasta / 'tentativas.db', agora=100))
        self.assertEqual(self.usuarios, {'autorizado': self.hash})

    def test_sessao_valida_e_isolada(self):
        estado = {}
        seg.iniciar_sessao(estado, 'autorizado', self.hash, agora=100)
        self.assertTrue(seg.sessao_autorizada(estado, self.usuarios, agora=200))
        self.assertFalse(seg.sessao_autorizada({}, self.usuarios, agora=200))
        self.assertNotIn(self.senha, str(estado))
        self.assertNotIn(self.hash, str(estado))

    def test_flag_ou_nome_sozinhos_nao_autorizam(self):
        self.assertFalse(seg.sessao_autorizada({'autenticado': True, 'usuario': 'autorizado'},
                                             self.usuarios, agora=100))

    def test_revogacao_invalida_sessao(self):
        estado = {}
        seg.iniciar_sessao(estado, 'autorizado', self.hash, agora=100)
        self.assertFalse(seg.sessao_autorizada(estado, {}, agora=101))
        self.assertEqual(estado, {})

    def test_troca_de_senha_invalida_sessao(self):
        estado = {}
        seg.iniciar_sessao(estado, 'autorizado', self.hash, agora=100)
        novo = seg.gerar_hash('Outra senha de teste 456!')
        self.assertFalse(seg.sessao_autorizada(estado, {'autorizado': novo}, agora=101))

    def test_expiracao_por_inatividade_e_duracao_maxima(self):
        for agora, ultima in [(1900, 100), (28900, 28899)]:
            estado = {}
            seg.iniciar_sessao(estado, 'autorizado', self.hash, agora=100)
            estado['ultima_atividade'] = ultima
            estado['dados'] = 'dados privados'
            self.assertFalse(seg.sessao_autorizada(estado, self.usuarios, agora=agora))
            self.assertEqual(estado, {})


if __name__ == '__main__':
    unittest.main()
