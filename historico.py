"""Histórico de arquivos e registros em PostgreSQL, independente da hospedagem."""

from datetime import datetime, timezone
import hashlib
import json
from pathlib import PurePath

import pandas as pd
from sqlalchemy import (Column, DateTime, Integer, JSON, LargeBinary, MetaData,
                        String, Table, create_engine, select, text)
from sqlalchemy.engine import make_url

METADATA = MetaData()
ARQUIVOS = Table(
    'dashboard_arquivos', METADATA,
    Column('id', String(64), primary_key=True),
    Column('nome', String(255), nullable=False),
    Column('usuario', String(64), nullable=False),
    Column('importado_em', DateTime(timezone=True), nullable=False),
    Column('linhas', Integer, nullable=False),
    Column('conteudo', LargeBinary, nullable=False),
    Column('dados', JSON, nullable=False),
)


def criar_engine_postgres(url: str):
    conexao = make_url(url)
    if conexao.get_backend_name() not in ('postgres', 'postgresql'):
        raise ValueError('O histórico publicado requer PostgreSQL.')
    if not conexao.host or not conexao.database:
        raise ValueError('Informe servidor e banco na conexão PostgreSQL.')
    conexao = conexao.set(drivername='postgresql+psycopg')
    if conexao.query.get('sslmode', 'require') not in ('require', 'verify-ca', 'verify-full'):
        raise ValueError('A conexão com o histórico requer TLS.')
    conexao = conexao.update_query_dict({'sslmode': conexao.query.get('sslmode', 'require')})
    return create_engine(conexao, pool_pre_ping=True, pool_size=3, max_overflow=2,
                         hide_parameters=True, connect_args={'connect_timeout': 10})


def preparar_arquivo(nome: str, conteudo: bytes, df: pd.DataFrame) -> dict:
    if not nome.lower().endswith('.xlsx'):
        raise ValueError('Envie arquivos .xlsx.')
    if not conteudo or len(conteudo) > 20 * 1024 * 1024:
        raise ValueError('Cada arquivo deve ter entre 1 byte e 20 MB.')
    if df.empty or not df.columns.is_unique:
        raise ValueError('A planilha está vazia ou tem cabeçalhos repetidos.')
    if 'Status' not in df or not ({'Data', 'data'} & set(df.columns)):
        raise ValueError('A planilha deve conter as colunas Data e Status.')
    # Mantém os valores originais do relatório e o próprio arquivo para rastreio.
    registros = json.loads(df.to_json(orient='records', date_format='iso', date_unit='s'))
    return {
        'id': hashlib.sha256(conteudo).hexdigest(),
        'nome': PurePath(nome.replace('\\', '/')).name[:255],
        'conteudo': conteudo,
        'dados': registros,
        'linhas': len(df),
    }


def consolidar_arquivos(quadros: list[tuple[str, pd.DataFrame]]) -> tuple[pd.DataFrame, int]:
    """Remove repetições exatas entre arquivos; preserva ocorrências internas.

    Não deduz uma identidade de agendamento nem substitui status distintos.
    Arquivos corrigidos devem substituir a versão anterior na seleção da tela.
    """
    if not quadros:
        return pd.DataFrame(), 0
    combinado = pd.concat([df for _, df in quadros], ignore_index=True)
    origens = [identificador for identificador, df in quadros for _ in range(len(df))]
    registros = json.loads(combinado.to_json(orient='records', date_format='iso', date_unit='s'))
    assinaturas = []
    for registro in registros:
        # 1 e 1.0 representam o mesmo número após a concatenação de colunas.
        normalizado = {k: int(v) if isinstance(v, float) and v.is_integer() else v
                       for k, v in registro.items()}
        texto = json.dumps(normalizado, sort_keys=True, ensure_ascii=False)
        assinaturas.append(hashlib.sha256(texto.encode()).hexdigest())
    chaves = pd.DataFrame({'origem': origens, 'assinatura': assinaturas})
    chaves['ocorrencia'] = chaves.groupby(['origem', 'assinatura']).cumcount()
    repetidas = chaves.duplicated(['assinatura', 'ocorrencia'])
    return combinado.loc[~repetidas].reset_index(drop=True), int(repetidas.sum())


class RepositorioHistorico:
    def __init__(self, engine):
        self.engine = engine
        with self.engine.begin() as conexao:
            self._bloquear(conexao)
            METADATA.create_all(conexao)

    def _bloquear(self, conexao):
        # Serializa importações concorrentes no PostgreSQL; não bloqueia leituras.
        if conexao.dialect.name == 'postgresql':
            conexao.execute(text('SELECT pg_advisory_xact_lock(:chave)'), {'chave': 482731})

    def importar(self, arquivos: list[dict], usuario: str) -> dict:
        if not usuario:
            raise ValueError('A importação precisa de um usuário identificado.')
        resultado = {'novos': 0, 'repetidos': 0}
        with self.engine.begin() as conexao:
            self._bloquear(conexao)
            existentes = set(conexao.scalars(select(ARQUIVOS.c.id)))
            for arquivo in arquivos:
                if arquivo['id'] in existentes:
                    resultado['repetidos'] += 1
                    continue
                conexao.execute(ARQUIVOS.insert().values(
                    **arquivo, usuario=usuario, importado_em=datetime.now(timezone.utc)
                ))
                existentes.add(arquivo['id'])
                resultado['novos'] += 1
        return resultado

    def listar(self) -> pd.DataFrame:
        colunas = ['id', 'nome', 'usuario', 'importado_em', 'linhas']
        consulta = select(*(ARQUIVOS.c[c] for c in colunas)).order_by(ARQUIVOS.c.importado_em, ARQUIVOS.c.id)
        with self.engine.connect() as conexao:
            return pd.DataFrame(conexao.execute(consulta).mappings().all(), columns=colunas)

    def carregar(self, ids: list[str]) -> tuple[pd.DataFrame, int]:
        if not ids:
            return pd.DataFrame(), 0
        consulta = select(ARQUIVOS.c.id, ARQUIVOS.c.dados).where(ARQUIVOS.c.id.in_(ids)).order_by(
            ARQUIVOS.c.importado_em, ARQUIVOS.c.id
        )
        with self.engine.connect() as conexao:
            quadros = [(linha.id, pd.DataFrame(linha.dados)) for linha in conexao.execute(consulta)]
        return consolidar_arquivos(quadros)
