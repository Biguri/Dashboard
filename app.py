"""Módulos 1 a 4: autenticação, leitura, processamento e dashboard interativo.

Execute com: python -m streamlit run app.py
"""

from io import BytesIO
import logging
import os
import re
import time
from numbers import Real
from typing import Optional

import pandas as pd
import streamlit as st
from historico import ErroHistorico, RepositorioHistorico, preparar_arquivo
from supabase_acesso import (AcessoNegado, ConfiguracaoInvalida, carregar_configuracao,
                             criar_cliente, entrar, restaurar, sair as encerrar_supabase)


LOGGER = logging.getLogger(__name__)
INATIVIDADE = 30 * 60
DURACAO_MAXIMA = 8 * 60 * 60
# Ajuste os valores para os nomes EXATOS das colunas da sua planilha.
COLUNAS = {"data": "data", "valor": "valor", "categoria": "categoria"}
# Regra confirmada: a unidade consta em Criado por; Cancelado é pelo paciente.
COLUNA_CLINICA = "clinica"
CANCELADO_SIGNIFICA_PACIENTE = True
UNIDADES = ("Fisioterapia", "Ivinhema", "Matriz", "Neurointensivo")


def inicializar_sessao() -> None:
    """Inicializa o estado individual de cada sessão."""
    st.session_state.setdefault("autenticado", False)
    st.session_state.setdefault("usuario", None)


def obter_cliente_supabase():
    """Cria um cliente por sessão; nunca compartilha tokens em cache global."""
    valores = {
        "SUPABASE_URL": os.environ.get("SUPABASE_URL"),
        "SUPABASE_PUBLISHABLE_KEY": os.environ.get("SUPABASE_PUBLISHABLE_KEY"),
    }
    for chave in valores:
        if valores[chave] is None:
            try:
                valores[chave] = st.secrets.get(chave)
            except FileNotFoundError:
                valores[chave] = None
    return criar_cliente(carregar_configuracao(valores))


def autenticar() -> None:
    """Valida o formulário antes de a página ser executada novamente."""
    usuario = st.session_state.get("login_usuario", "").strip()
    senha = st.session_state.get("login_senha", "")
    try:
        cliente = obter_cliente_supabase()
        sessao = entrar(cliente, usuario, senha)
        agora = time.time()
        st.session_state.update({
            "autenticado": True,
            "usuario": sessao.usuario,
            "sessao_supabase": sessao,
            "cliente_supabase": cliente,
            "inicio_sessao": agora,
            "ultima_atividade": agora,
            "erro_login": False,
        })
    except (AcessoNegado, ConfiguracaoInvalida, ValueError) as erro:
        LOGGER.warning("Tentativa de acesso recusada na etapa %s.",
                       getattr(erro, "etapa", "configuracao"))
        st.session_state["autenticado"] = False
        st.session_state["usuario"] = None
        st.session_state.pop("sessao_supabase", None)
        st.session_state.pop("cliente_supabase", None)
        st.session_state["erro_login"] = True
    finally:
        st.session_state.pop("login_senha", None)


def exibir_login() -> None:
    """Exibe somente o formulário e, em caso de falha, uma mensagem."""
    with st.form("formulario_login"):
        st.text_input("Usuário", key="login_usuario", max_chars=64)
        st.text_input("Senha", type="password", key="login_senha", max_chars=1024)
        st.form_submit_button("Entrar", on_click=autenticar)

    if st.session_state.get("erro_login", False):
        st.error("Acesso não autorizado. Confira usuário e senha.")


def _limpar_sessao() -> None:
    st.session_state.clear()
    carregar_dados.clear()


def restaurar_sessao(agora=None, atualizar=True) -> bool:
    """Revalida token e perfil; expiração ou inconsistência falha fechada."""
    agora = time.time() if agora is None else agora
    sessao = st.session_state.get("sessao_supabase")
    inicio = st.session_state.get("inicio_sessao")
    ultima = st.session_state.get("ultima_atividade")
    tempos_validos = all(isinstance(valor, (int, float)) for valor in (inicio, ultima))
    if (st.session_state.get("autenticado") is not True or sessao is None
            or not tempos_validos or inicio > ultima or ultima > agora
            or agora - inicio >= DURACAO_MAXIMA or agora - ultima >= INATIVIDADE):
        _limpar_sessao()
        return False
    try:
        cliente = obter_cliente_supabase()
        renovada = restaurar(
            cliente, sessao.usuario, sessao.access_token, sessao.refresh_token
        )
    except (AcessoNegado, ConfiguracaoInvalida, ValueError):
        _limpar_sessao()
        return False
    st.session_state["sessao_supabase"] = renovada
    st.session_state["usuario"] = renovada.usuario
    st.session_state["cliente_supabase"] = cliente
    if atualizar:
        st.session_state["ultima_atividade"] = agora
    return True


def sair() -> None:
    """Encerra o acesso e remove os dados desta sessão."""
    cliente = st.session_state.get("cliente_supabase")
    if cliente is not None:
        encerrar_supabase(cliente)
    _limpar_sessao()


@st.cache_data(scope="session")
def carregar_dados(arquivo, modificacao_ns: int = 0) -> pd.DataFrame:
    """Lê a primeira aba; a modificação local participa da chave do cache."""
    try:
        return pd.read_excel(arquivo, engine="openpyxl")
    except TypeError as erro:
        if str(erro) != "expected <class 'float'>":
            raise
        # Alguns arquivos exportados contêm estilos incompatíveis com openpyxl.
        # A primeira tentativa pode ter avançado o cursor do upload.
        if hasattr(arquivo, "seek"):
            arquivo.seek(0)
        LOGGER.warning("Falha de conversão no openpyxl; tentando calamine.")
        return pd.read_excel(arquivo, engine="calamine")


def identificar_clinica(criado_por) -> str:
    """Extrai as unidades citadas, sem atribuir nomes ambíguos a uma só clínica."""
    if pd.isna(criado_por):
        return "Clínica não informada"
    unidades = [
        unidade for unidade in UNIDADES
        if re.search(rf"\b{re.escape(unidade)}\b", str(criado_por), flags=re.IGNORECASE)
    ]
    if not unidades:
        return "Clínica não informada"
    if len(unidades) > 1:
        return "Múltiplas clínicas: " + " / ".join(unidades)
    return unidades[0]


def limpar_dados(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Preserva a origem e registros parciais; informa conversões inválidas.

    Textos monetários usam formato brasileiro (1.234,56); números nativos
    permanecem numéricos. Datas textuais usam dia/mês/ano e números de datas
    usam o calendário Excel 1900. Duplicatas são preservadas.
    """
    dados = df.copy(deep=True)
    # Compatibilidade com os cabeçalhos do relatório de agendamentos recebido.
    for origem, destino in (("Data", COLUNAS["data"]), ("Valor", COLUNAS["valor"]), ("Área", COLUNAS["categoria"])):
        if origem in dados.columns and destino not in dados.columns:
            dados = dados.rename(columns={origem: destino})
    avisos = []
    for coluna in dados.columns:
        dados[coluna] = dados[coluna].map(
            lambda valor: (valor.strip() or pd.NA) if isinstance(valor, str) else valor
        )
    dados = dados.dropna(how="all").reset_index(drop=True)

    if "Criado por" in dados:
        dados[COLUNA_CLINICA] = dados["Criado por"].map(identificar_clinica)
        ambiguos = int(dados[COLUNA_CLINICA].str.startswith("Múltiplas clínicas:").sum())
        ausentes = int(dados[COLUNA_CLINICA].eq("Clínica não informada").sum())
        if ambiguos:
            avisos.append(
                f"{ambiguos} registros mencionam múltiplas clínicas em 'Criado por'. "
                "Eles aparecem em grupos separados, sem duplicar as contagens."
            )
        if ausentes:
            avisos.append(
                f"{ausentes} registros não identificam uma unidade conhecida em 'Criado por'. "
                "Eles aparecem como 'Clínica não informada'."
            )

    for papel, coluna in COLUNAS.items():
        if coluna not in dados.columns:
            avisos.append(f"Coluna '{coluna}' ausente: ajuste COLUNAS para usar {papel}.")

    def converter_moeda(valor):
        if isinstance(valor, str):
            valor = valor.replace("R$", "").replace("\u00a0", "").replace(" ", "")
            if "," in valor:
                valor = valor.replace(".", "").replace(",", ".")
            elif re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3})+", valor):
                valor = valor.replace(".", "")
        numero = pd.to_numeric(valor, errors="coerce")
        return float("nan") if numero in (float("inf"), float("-inf")) else numero

    def converter_data(valor):
        if pd.isna(valor):
            return pd.NaT
        if isinstance(valor, Real):
            return pd.to_datetime(valor, unit="D", origin="1899-12-30", errors="coerce")
        if isinstance(valor, str) and re.match(r"^\d{4}-\d{2}-\d{2}", valor):
            return pd.to_datetime(valor, errors="coerce")
        return pd.to_datetime(valor, dayfirst=True, errors="coerce")

    for papel, conversor in (("valor", converter_moeda), ("data", converter_data)):
        coluna = COLUNAS[papel]
        if coluna in dados.columns:
            original = dados[coluna]
            convertido = original.map(conversor)
            invalidos = int((original.notna() & convertido.isna()).sum())
            dados[coluna] = convertido
            if invalidos:
                avisos.append(
                    f"{invalidos} valor(es) inválido(s) em '{coluna}' foram tratados como ausentes."
                )
    return dados, avisos


def calcular_kpis(df: pd.DataFrame) -> dict:
    """Ticket médio considera apenas registros com valor monetário válido."""
    valores = df[COLUNAS["valor"]].dropna() if COLUNAS["valor"] in df else pd.Series(dtype=float)
    categorias = df[COLUNAS["categoria"]] if COLUNAS["categoria"] in df else None
    return {
        "total_registros": len(df),
        "faturamento_total": float(valores.sum()) if not valores.empty else None,
        "ticket_medio": float(valores.mean()) if not valores.empty else None,
        "total_categorias": int(categorias.nunique()) if categorias is not None else None,
    }


def analisar_cancelamentos(
    df: pd.DataFrame,
    coluna_clinica: Optional[str] = None,
    cancelado_significa_paciente: bool = False,
) -> tuple[dict, Optional[pd.DataFrame]]:
    """Conta linhas de agendamento por status, sem deduzir autor ou unidade.

    Faltas não são cancelamentos. Status genéricos permanecem sem autor,
    salvo regra explicitamente confirmada. Unidades sem cancelamentos também
    aparecem no resumo; linhas sem unidade são mantidas em grupo próprio.
    """
    resumo = dict.fromkeys(
        ("total_cancelamentos", "paciente", "profissional", "nao_informado")
    )
    if "Status" not in df:
        return resumo, None

    status = df["Status"].astype("string").str.strip().str.casefold()
    cancelados = status.str.startswith("cancelad", na=False)
    paciente = status.eq("cancelado pelo paciente").fillna(False)
    if cancelado_significa_paciente:
        paciente = paciente | status.eq("cancelado").fillna(False)
    profissional = status.eq("cancelado pelo profissional").fillna(False)
    nao_informado = cancelados & ~paciente & ~profissional
    resumo = {
        "total_cancelamentos": int(cancelados.sum()),
        "paciente": int(paciente.sum()),
        "profissional": int(profissional.sum()),
        "nao_informado": int(nao_informado.sum()),
    }
    if coluna_clinica is None or coluna_clinica not in df:
        return resumo, None

    clinicas = df[coluna_clinica].astype("string").str.strip()
    clinicas = clinicas.replace("", pd.NA).fillna("Clínica não informada")
    contagens = pd.DataFrame({
        "Clínica": clinicas,
        "Total de registros": 1,
        "Cancelamentos": cancelados.astype(int),
        "Paciente": paciente.astype(int),
        "Profissional": profissional.astype(int),
        "Responsável não informado": nao_informado.astype(int),
    })
    por_clinica = contagens.groupby("Clínica", as_index=False, sort=True).sum()
    return resumo, por_clinica


def filtrar_dados(df: pd.DataFrame, meses=None, clinicas=None, anos=None) -> pd.DataFrame:
    """None mantém todos; uma seleção vazia retorna zero registros."""
    mascara = pd.Series(True, index=df.index)
    if meses is not None and COLUNAS["data"] in df:
        periodos = df[COLUNAS["data"]].dt.strftime("%Y-%m").fillna("Sem data")
        mascara &= periodos.isin(meses)
    if clinicas is not None and COLUNA_CLINICA in df:
        mascara &= df[COLUNA_CLINICA].isin(clinicas)
    if anos is not None and COLUNAS["data"] in df:
        mascara &= df[COLUNAS["data"]].dt.strftime("%Y").fillna("Sem data").isin(anos)
    return df.loc[mascara].copy()


def preparar_evolucao_diaria(df: pd.DataFrame) -> pd.DataFrame:
    """Soma cancelamentos por data do agendamento, excluindo datas ausentes."""
    series = ["Paciente", "Profissional", "Responsável não informado"]
    if COLUNAS["data"] not in df or "Status" not in df:
        return pd.DataFrame(columns=series)
    dados = df.dropna(subset=[COLUNAS["data"]]).copy()
    if dados.empty:
        return pd.DataFrame(columns=series)
    dados["dia"] = dados[COLUNAS["data"]].dt.strftime("%Y-%m-%d")
    _, tabela = analisar_cancelamentos(dados, "dia", CANCELADO_SIGNIFICA_PACIENTE)
    tabela.index = pd.to_datetime(tabela["Clínica"])
    dias = pd.date_range(tabela.index.min(), tabela.index.max(), freq="D", name="Data")
    return tabela[series].reindex(dias, fill_value=0)


def preparar_evolucao_mensal(df: pd.DataFrame) -> pd.DataFrame:
    series = ["Paciente", "Profissional", "Responsável não informado"]
    if COLUNAS["data"] not in df or "Status" not in df:
        return pd.DataFrame(columns=series)
    dados = df.dropna(subset=[COLUNAS["data"]]).copy()
    if dados.empty:
        return pd.DataFrame(columns=series)
    dados["mes"] = dados[COLUNAS["data"]].dt.strftime("%Y-%m")
    _, tabela = analisar_cancelamentos(dados, "mes", CANCELADO_SIGNIFICA_PACIENTE)
    return tabela.rename(columns={"Clínica": "Mês"}).set_index("Mês")


def criar_ranking_cancelamentos(df: pd.DataFrame, responsavel: str) -> pd.DataFrame:
    """Agrupa cancelamentos do responsável, usando ID para distinguir pacientes."""
    if responsavel not in ("paciente", "profissional"):
        raise ValueError("Responsável deve ser paciente ou profissional.")
    coluna = "Paciente" if responsavel == "paciente" else "Profissional"
    colunas = [coluna, "Cancelamentos"]
    if "Status" not in df or coluna not in df:
        return pd.DataFrame(columns=colunas)
    status = df["Status"].astype("string").str.strip().str.casefold()
    mascara = status.eq(f"cancelado pelo {responsavel}").fillna(False)
    if responsavel == "paciente" and CANCELADO_SIGNIFICA_PACIENTE:
        mascara |= status.eq("cancelado").fillna(False)
    dados = df.loc[mascara].copy()
    if dados.empty:
        return pd.DataFrame(columns=colunas)

    nomes = dados[coluna].astype("string").str.strip().replace("", pd.NA)
    dados[coluna] = nomes.fillna(f"{coluna} não informado")
    dados["chave"] = "nome:" + dados[coluna].str.casefold()
    if responsavel == "paciente" and "ID paciente" in dados:
        ids = dados["ID paciente"].astype("string").str.strip().replace("", pd.NA)
        dados["ID paciente"] = ids
        dados.loc[ids.notna(), "chave"] = "id:" + ids[ids.notna()]

    grupo = dados.groupby("chave", sort=False)
    ranking = grupo.agg(**{coluna: (coluna, "first"), "Cancelamentos": (coluna, "size")})
    if responsavel == "paciente" and "ID paciente" in dados:
        ranking["ID paciente"] = grupo["ID paciente"].first()
    return ranking.sort_values(["Cancelamentos", coluna], ascending=[False, True]).reset_index(drop=True)


def exibir_rankings(df: pd.DataFrame) -> None:
    st.subheader("Quem mais cancelou no período")
    st.caption("Considera todos os registros dos filtros atuais, não apenas as 100 linhas da tabela final.")
    limite = st.selectbox("Quantidade de nomes nos gráficos", [10, 20, 50], key="limite_ranking")
    abas = st.tabs(["Pacientes", "Profissionais"])
    for aba, responsavel, coluna in zip(abas, ("paciente", "profissional"), ("Paciente", "Profissional")):
        with aba:
            st.caption(f"Somente cancelamentos pelo {responsavel}. Ordenação por quantidade de agendamentos cancelados.")
            if coluna not in df or "Status" not in df:
                st.info(f"As colunas Status e {coluna} são necessárias para este ranking.")
                continue
            ranking = criar_ranking_cancelamentos(df, responsavel)
            if ranking.empty:
                st.info("Nenhum cancelamento deste responsável no recorte selecionado.")
                continue
            if responsavel == "paciente":
                st.caption("Pacientes são identificados pelo ID. Na ausência de ID, o agrupamento usa o nome.")
            else:
                st.caption("Profissionais são agrupados pelo nome informado na planilha.")
            grafico = ranking.head(limite).copy()
            grafico["Nome"] = grafico[coluna]
            if "ID paciente" in grafico:
                grafico["Nome"] += " · ID " + grafico["ID paciente"].fillna("não informado")
            st.bar_chart(grafico, x="Nome", y="Cancelamentos", horizontal=True,
                         sort="-Cancelamentos", height=max(320, len(grafico) * 30),
                         color="#2563eb" if responsavel == "paciente" else "#ea580c")
            st.caption(f"Ranking completo: {len(ranking)} nomes/grupos, {int(ranking['Cancelamentos'].sum())} cancelamentos.")
            st.dataframe(ranking, hide_index=True)


def resumir_movimentacao(df: pd.DataFrame, coluna=None) -> pd.DataFrame:
    """Categorias exclusivas: sua soma reconcilia com o total de agendamentos."""
    status = df.get('Status', pd.Series(pd.NA, index=df.index, dtype='string'))
    status = status.astype('string').str.strip().str.casefold()
    dados = pd.DataFrame(index=df.index)
    dados['Total'] = 1
    dados['Atendidos'] = status.eq('atendido').fillna(False).astype(int)
    dados['Faltas'] = status.isin(['faltou', 'faltou (com aviso prévio)']).astype(int)
    dados['Cancelamentos'] = status.str.startswith('cancelad', na=False).astype(int)
    dados['Agendados'] = status.isin(['agendado', 'agendada', 'agendados']).astype(int)
    dados['Outros'] = 1 - dados[['Atendidos', 'Faltas', 'Cancelamentos', 'Agendados']].sum(axis=1)
    if coluna is not None:
        if coluna not in df:
            return pd.DataFrame(columns=[coluna] + dados.columns.tolist())
        dados[coluna] = df[coluna].astype('string').str.strip().replace('', pd.NA).fillna('Não informado')
        resultado = dados.groupby(coluna, as_index=False).sum().sort_values('Total', ascending=False)
    else:
        resultado = pd.DataFrame([dados.sum()])
    if 'Status' not in df:
        for nome in ['Atendidos', 'Faltas', 'Cancelamentos', 'Agendados', 'Outros']:
            resultado[nome] = pd.NA
    return resultado


def exibir_quantidades(df: pd.DataFrame) -> None:
    st.subheader('Quantidades por profissional e unidade')
    st.caption('Contagens de agendamentos por status no período filtrado, incluindo os que não foram cancelados.')
    abas = st.tabs(['Por profissional', 'Por unidade (clínica)', 'Totais por status'])
    for aba, coluna, rotulo in zip(abas[:2], ['Profissional', COLUNA_CLINICA], ['Profissional', 'Clínica']):
        with aba:
            if coluna not in df:
                st.info(f'A identificação de {rotulo.lower()} não está disponível neste arquivo.')
                continue
            tabela = resumir_movimentacao(df, coluna).rename(columns={coluna: rotulo})
            if tabela.empty:
                st.info('Nenhum registro no recorte selecionado.')
                continue
            st.caption('Gráfico com os 20 maiores totais; a tabela contém todos os grupos.')
            st.bar_chart(tabela.head(20), x=rotulo, y='Total', horizontal=True,
                         sort='-Total', height=max(320, min(len(tabela), 20) * 28))
            st.dataframe(tabela, hide_index=True)
            if coluna == COLUNA_CLINICA:
                st.caption('Grupos com múltiplas clínicas ou sem unidade identificada são preservados, sem duplicar agendamentos.')
    with abas[2]:
        if 'Status' in df:
            status = df['Status'].astype('string').str.strip().replace('', pd.NA).fillna('Não informado')
            st.dataframe(status.value_counts().rename_axis('Status').reset_index(name='Quantidade'), hide_index=True)
        else:
            st.info('Coluna Status não disponível.')


def exibir_dashboard(df: pd.DataFrame) -> None:
    """Aplica o mesmo recorte aos cartões, gráficos e tabela final."""
    st.sidebar.header("Filtros")
    meses = clinicas = anos = None
    if COLUNAS["data"] in df:
        opcoes_anos = sorted(df[COLUNAS["data"]].dt.strftime("%Y").dropna().unique())
        ano = st.sidebar.selectbox("Ano", ["Todos os anos"] + opcoes_anos)
        anos = None if ano == "Todos os anos" else [ano]
        base_ano = filtrar_dados(df, anos=anos)
        opcoes = sorted(base_ano[COLUNAS["data"]].dt.strftime("%Y-%m").dropna().unique())
        if base_ano[COLUNAS["data"]].isna().any():
            opcoes.append("Sem data")
        meses = st.sidebar.multiselect("Mês do agendamento", opcoes, default=opcoes)
    if COLUNA_CLINICA in df:
        opcoes_clinicas = sorted(df[COLUNA_CLINICA].dropna().unique())
        clinicas = st.sidebar.multiselect("Clínica / grupo", opcoes_clinicas, default=opcoes_clinicas)
    filtrado = filtrar_dados(df, meses, clinicas, anos)
    st.caption(f"{len(filtrado):,} de {len(df):,} registros no recorte selecionado.".replace(",", "."))

    resumo, por_clinica = analisar_cancelamentos(
        filtrado, COLUNA_CLINICA, CANCELADO_SIGNIFICA_PACIENTE
    )
    cartoes = st.columns(4)
    indicadores = [
        ("Agendamentos", len(filtrado)),
        ("Cancelamentos", resumo["total_cancelamentos"]),
        ("Pelo paciente", resumo["paciente"]),
        ("Pelo profissional", resumo["profissional"]),
    ]
    for cartao, (rotulo, valor) in zip(cartoes, indicadores):
        cartao.metric(rotulo, "Indisponível" if valor is None else f"{valor:,}".replace(",", "."))
    movimentacao = resumir_movimentacao(filtrado).iloc[0]
    for cartao, chave, rotulo in zip(st.columns(4), ['Atendidos', 'Faltas', 'Agendados', 'Outros'],
                                    ['Atendidos', 'Faltas', 'Status Agendado', 'Outros status']):
        valor = movimentacao[chave]
        cartao.metric(rotulo, 'Indisponível' if pd.isna(valor) else f'{int(valor):,}'.replace(',', '.'))
    st.caption('Agendamentos = total de registros. Status Agendado = apenas registros com esse status. '
               'Atendidos = status Atendido; Faltas inclui Faltou e Faltou (com aviso prévio). '
               'Os demais status, incluindo Atendimento Realizado Previamente, aparecem em Outros.')
    st.caption("Cancelado = paciente. Faltas não são cancelamentos. Contagens por linha de agendamento.")

    if filtrado.empty:
        st.info("Nenhum agendamento corresponde aos filtros selecionados.")
    elif resumo["total_cancelamentos"] is None:
        st.warning("A coluna Status é necessária para analisar os cancelamentos.")
    else:
        if resumo["nao_informado"]:
            st.warning(f"{resumo['nao_informado']} cancelamentos sem responsável identificado no recorte.")
        series = ["Paciente", "Profissional", "Responsável não informado"]
        cores = ["#2563eb", "#ea580c", "#64748b"]
        st.subheader("Cancelamentos por clínica e responsável")
        if por_clinica is not None:
            st.bar_chart(por_clinica, x="Clínica", y=series, color=cores,
                         horizontal=True, stack=False, height=420)
            st.caption("Unidades extraídas de Criado por. Grupos com múltiplas clínicas não são divididos entre unidades.")
            with st.expander("Ver resumo por clínica"):
                st.dataframe(por_clinica, hide_index=True)
        else:
            st.info("Não há identificação de clínica neste arquivo.")

        st.subheader("Evolução dos cancelamentos")
        frequencia = st.radio("Agrupar evolução por", ["Dia", "Mês"], horizontal=True)
        evolucao = preparar_evolucao_diaria(filtrado) if frequencia == "Dia" else preparar_evolucao_mensal(filtrado)
        if evolucao.empty:
            st.info("Não há datas válidas para exibir a evolução.")
        else:
            st.line_chart(evolucao, y=series, color=cores, height=320)
            st.caption("Data do agendamento, não a data em que o cancelamento foi registrado.")
            with st.expander("Comparativo mês a mês"):
                st.dataframe(preparar_evolucao_mensal(filtrado))
        if COLUNAS["data"] in filtrado and filtrado[COLUNAS["data"]].isna().any():
            st.caption("Registros sem data permanecem nos cartões e na tabela, mas não entram na evolução diária.")

    exibir_quantidades(filtrado)
    exibir_rankings(filtrado)

    st.subheader("Últimos 100 registros do recorte")
    st.caption("Ordem original do arquivo, após aplicar os filtros.")
    st.dataframe(filtrado.tail(100), hide_index=True)


def exibir_historico() -> Optional[pd.DataFrame]:
    """Importação explícita; o RLS limita os dados à clínica da sessão."""
    sessao = st.session_state.get("sessao_supabase")
    cliente = st.session_state.get("cliente_supabase")
    if sessao is None or cliente is None:
        st.stop()
    try:
        repo = RepositorioHistorico(cliente)
        st.subheader("Histórico de agendamentos")
        st.caption(f"Clínica: {sessao.clinica_nome}. O banco aplica o isolamento desta conta.")
        with st.form("importar_historico"):
            uploads = st.file_uploader("Adicionar planilhas ao histórico", type=["xlsx"], accept_multiple_files=True)
            gravar = st.form_submit_button("Armazenar arquivos")
        if gravar:
            if not uploads:
                st.warning("Selecione ao menos um arquivo.")
            elif sum(arquivo.size for arquivo in uploads) > 100 * 1024 * 1024:
                st.warning("Envie no máximo 100 MB por lote e 20 MB por arquivo.")
            else:
                preparados = []
                with st.spinner("Validando e armazenando o lote..."):
                    for arquivo in uploads:
                        if arquivo.size > 20 * 1024 * 1024:
                            raise ValueError("Cada arquivo pode ter no máximo 20 MB.")
                        conteudo = arquivo.getvalue()
                        dados = carregar_dados(BytesIO(conteudo))
                        preparados.append(preparar_arquivo(arquivo.name, conteudo, dados))
                    if not restaurar_sessao():
                        st.rerun()
                    repo = RepositorioHistorico(st.session_state["cliente_supabase"])
                    resultado = repo.importar(preparados)
                    st.session_state["arquivos_recem_enviados"] = [
                        arquivo["id"] for arquivo in preparados
                    ]
                    st.session_state["modo_analise_historico"] = (
                        "Somente arquivos recém-enviados"
                    )
                st.success(f"{resultado['novos']} arquivo(s) armazenado(s); {resultado['repetidos']} já estavam no histórico.")
        st.button("Atualizar histórico")
        lista = repo.listar()
        if lista.empty:
            st.info("Nenhum arquivo armazenado ainda.")
            return None
        rotulos = {r.id: f"{r.nome} · {r.id[:8]}" for r in lista.itertuples()}
        ids_disponiveis = lista['id'].tolist()
        recentes = [
            identificador
            for identificador in st.session_state.get("arquivos_recem_enviados", [])
            if identificador in ids_disponiveis
        ]
        modos = ["Somente arquivos recém-enviados", "Selecionar arquivos do histórico"]
        if "modo_analise_historico" not in st.session_state:
            st.session_state["modo_analise_historico"] = modos[0] if recentes else modos[1]
        modo = st.radio(
            "Quais arquivos analisar?",
            modos,
            key="modo_analise_historico",
            horizontal=True,
        )
        if modo == modos[0]:
            escolhidos = recentes
            if not recentes:
                st.info("Envie um arquivo nesta sessão ou escolha arquivos do histórico.")
        else:
            escolhidos = st.multiselect(
                "Arquivos incluídos na análise",
                ids_disponiveis,
                default=ids_disponiveis,
                format_func=rotulos.get,
            )
        st.caption("Para um relatório corrigido, selecione a versão desejada e desmarque a anterior. Os originais continuam armazenados.")
        with st.expander("Arquivos armazenados"):
            st.dataframe(lista.drop(columns=['id']).rename(columns={
                'nome': 'Arquivo', 'importado_por': 'Importado por',
                'importado_em': 'Importação (UTC)', 'linhas': 'Registros'
            }), hide_index=True)
        df, removidas = repo.carregar(escolhidos)
        if removidas:
            st.info(f"{removidas} repetições idênticas entre arquivos foram excluídas desta análise.")
        if df.empty:
            st.info("Selecione arquivos para visualizar o histórico.")
            return None
        return df
    except ValueError:
        st.error("Não foi possível importar. Confira se todos os arquivos são .xlsx de até 20 MB e têm Data e Status.")
        return None
    except ErroHistorico as erro:
        LOGGER.error("Falha na operação do histórico (%s).", type(erro).__name__)
        st.error("Não foi possível acessar ou atualizar o histórico no Supabase. Nenhum lote é gravado parcialmente.")
        return None


def exibir_area_autenticada() -> None:
    """Disponibiliza a leitura de dados somente após a autenticação."""
    if st.session_state.get("autenticado") is not True:
        st.stop()
    verificar_sessao_periodicamente()
    st.sidebar.button("Sair", on_click=sair)
    st.title("Dashboard de Agendamentos")
    st.sidebar.header("Dados da clínica")
    st.sidebar.caption(
        "Uploads são armazenados no Supabase e isolados pela clínica da conta."
    )
    df = exibir_historico()

    if df is not None:
        df, avisos = limpar_dados(df)
        chave_agendamento = ["ID paciente", COLUNAS["data"], "Hora Início", "Hora Fim", "Profissional"]
        if all(c in df for c in chave_agendamento):
            semelhantes = int(df.duplicated(chave_agendamento, keep=False).sum())
            if semelhantes:
                st.warning(f"{semelhantes} registros têm o mesmo paciente, data, horário e profissional. Eles foram preservados. Confira relatórios sobrepostos na seleção de arquivos antes de interpretar os totais.")
        if avisos:
            with st.expander("Observações sobre a base completa"):
                for aviso in avisos:
                    st.warning(aviso)
        exibir_dashboard(df)


@st.fragment(run_every="60s")
def verificar_sessao_periodicamente() -> None:
    """Revalida sem renovar a atividade quando a página permanece aberta."""
    if not restaurar_sessao(atualizar=False):
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="Dashboard", layout="wide")
    inicializar_sessao()

    estava_autenticado = st.session_state.get("autenticado") is True
    if not estava_autenticado or not restaurar_sessao():
        exibir_login()
        st.stop()

    exibir_area_autenticada()


if __name__ == "__main__":
    main()
