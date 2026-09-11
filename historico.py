"""Preparação local e histórico persistente pela Data API do Supabase."""

import base64
import hashlib
import json
from pathlib import PurePath

import pandas as pd


class ErroHistorico(RuntimeError):
    pass


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
    def __init__(self, cliente):
        self.cliente = cliente

    def importar(self, arquivos: list[dict]) -> dict:
        if not 1 <= len(arquivos) <= 20:
            raise ValueError('O lote deve conter entre 1 e 20 arquivos.')
        lote = [{
            'id': arquivo['id'],
            'nome': arquivo['nome'],
            'linhas': arquivo['linhas'],
            'dados': arquivo['dados'],
            'conteudo_base64': base64.b64encode(arquivo['conteudo']).decode('ascii'),
        } for arquivo in arquivos]
        try:
            resposta = self.cliente.rpc(
                'importar_dashboard_arquivos', {'p_arquivos': lote}
            ).execute()
            resultado = resposta.data
            if (not isinstance(resultado, dict)
                    or set(resultado) != {'novos', 'repetidos'}
                    or any(type(resultado[chave]) is not int or resultado[chave] < 0
                           for chave in ('novos', 'repetidos'))):
                raise ErroHistorico('Resposta inválida ao importar o histórico.')
            return resultado
        except ErroHistorico:
            raise
        except Exception:
            raise ErroHistorico('Operação do histórico indisponível.') from None

    def listar(self) -> pd.DataFrame:
        colunas = ['id', 'nome', 'importado_por', 'importado_em', 'linhas']
        try:
            resposta = (
                self.cliente.table('dashboard_arquivos')
                .select(','.join(colunas))
                .order('importado_em')
                .order('id')
                .execute()
            )
            return pd.DataFrame(resposta.data or [], columns=colunas)
        except Exception:
            raise ErroHistorico('Operação do histórico indisponível.') from None

    def carregar(self, ids: list[str]) -> tuple[pd.DataFrame, int]:
        if not ids:
            return pd.DataFrame(), 0
        try:
            resposta = (
                self.cliente.table('dashboard_arquivos')
                .select('id,dados')
                .in_('id', ids)
                .order('importado_em')
                .order('id')
                .execute()
            )
            quadros = [(linha['id'], pd.DataFrame(linha['dados'])) for linha in (resposta.data or [])]
            return consolidar_arquivos(quadros)
        except Exception:
            raise ErroHistorico('Operação do histórico indisponível.') from None
