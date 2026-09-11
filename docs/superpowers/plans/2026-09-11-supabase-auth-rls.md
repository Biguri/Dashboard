# Supabase Auth e RLS por clínica — Plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Autenticar contas por nome e senha no Supabase e garantir no banco que cada conta leia e importe somente os arquivos de sua clínica.

**Architecture:** O Streamlit autentica com Supabase Auth usando um e-mail técnico derivado do nome e conserva os tokens apenas na sessão. Um repositório sobre `supabase-py` usa a Data API com o JWT do usuário; tabelas, grants, RLS e uma RPC transacional impõem o isolamento por `clinica_id`.

**Tech Stack:** Python 3.14, Streamlit 1.63, pandas 3.0, supabase-py, PostgreSQL, Supabase Auth, PostgREST RPC e unittest.

**Spec:** `docs/superpowers/specs/2026-09-11-supabase-auth-rls-design.md`

## Global Constraints

- Cada conta pertence a uma clínica e cada clínica tem uma conta nesta primeira versão.
- Não há cadastro público; contas, clínicas e perfis são administrados no Supabase.
- O app recebe somente `SUPABASE_URL` e `SUPABASE_PUBLISHABLE_KEY`; nunca usa secret key, `service_role` ou senha PostgreSQL.
- O JWT do usuário acompanha as operações e os tokens ficam somente em `st.session_state`.
- `anon` não acessa tabelas/RPC; `authenticated` recebe somente leitura e importação.
- O aplicativo não oferece `UPDATE` ou `DELETE`.
- Segredos, tokens, `.env` e `.streamlit/secrets.toml` permanecem fora do Git e dos logs.
- Os arquivos Excel continuam em `dashboard_arquivos.conteudo`; Storage fica fora deste plano.

## Estrutura de arquivos

- Criar `supabase/migrations/202609110001_auth_rls_clinicas.sql` para esquema e autorização.
- Criar `test_migracao_supabase.py` para o contrato estático de segurança do SQL.
- Criar `supabase_acesso.py` e `test_supabase_acesso.py` para autenticação/sessão.
- Modificar `historico.py` e `test_historico.py` para usar a Data API.
- Modificar `app.py`, `test_acesso.py` e `test_historico_interface.py` para integrar a UI.
- Modificar `requirements.txt`, `.gitignore` e `README.md`; remover o fluxo local obsoleto.

---

### Task 1: Esquema, grants, RLS e RPC atômica

**Files:**
- Create: `supabase/migrations/202609110001_auth_rls_clinicas.sql`
- Create: `test_migracao_supabase.py`

**Interfaces:**
- Consumes: JWT do Supabase Auth e `p_arquivos jsonb`.
- Produces: `clinicas`, `perfis`, `dashboard_arquivos` e `importar_dashboard_arquivos(jsonb) -> jsonb`.

- [ ] **Step 1: Escrever o teste de contrato SQL que falha**

```python
from pathlib import Path
import unittest

class MigracaoSupabaseTests(unittest.TestCase):
    def setUp(self):
        self.sql = Path("supabase/migrations/202609110001_auth_rls_clinicas.sql").read_text().lower()

    def test_rls_e_grants_minimos(self):
        for tabela in ("clinicas", "perfis", "dashboard_arquivos"):
            self.assertIn(f"alter table public.{tabela} enable row level security", self.sql)
        self.assertIn("revoke all on table public.dashboard_arquivos from anon", self.sql)
        self.assertIn("grant select, insert on table public.dashboard_arquivos to authenticated", self.sql)

    def test_politicas_e_rpc_usam_identidade(self):
        self.assertGreaterEqual(self.sql.count("(select auth.uid())"), 4)
        self.assertIn("importado_por = (select auth.uid())", self.sql)
        self.assertIn("security invoker", self.sql)
        self.assertNotIn("security definer", self.sql)
        self.assertIn("grant execute on function public.importar_dashboard_arquivos(jsonb) to authenticated", self.sql)
```

- [ ] **Step 2: Rodar o teste e confirmar a falha**

Run: `python -m unittest test_migracao_supabase.py -v`

Expected: ERROR `FileNotFoundError`.

- [ ] **Step 3: Criar as tabelas e políticas**

Usar este modelo na migração:

```sql
create extension if not exists pgcrypto with schema extensions;
create table public.clinicas (
  id uuid primary key default gen_random_uuid(),
  nome text not null unique check (length(btrim(nome)) between 1 and 120),
  criada_em timestamptz not null default now()
);
create table public.perfis (
  user_id uuid primary key references auth.users(id) on delete cascade,
  clinica_id uuid not null unique references public.clinicas(id),
  nome_usuario text not null unique check (
    nome_usuario ~ '^[a-z0-9_.@-]{1,64}$' and nome_usuario = lower(nome_usuario)
  ),
  criado_em timestamptz not null default now()
);
create table public.dashboard_arquivos (
  clinica_id uuid not null references public.clinicas(id),
  id text not null check (id ~ '^[0-9a-f]{64}$'),
  nome text not null check (length(nome) between 1 and 255),
  importado_por uuid not null references auth.users(id),
  importado_em timestamptz not null default now(),
  linhas integer not null check (linhas > 0),
  conteudo bytea not null check (octet_length(conteudo) between 1 and 20971520),
  dados jsonb not null check (jsonb_typeof(dados) = 'array'),
  primary key (clinica_id, id)
);
create index dashboard_arquivos_clinica_data_idx
  on public.dashboard_arquivos (clinica_id, importado_em, id);
```

Habilitar e forçar RLS nas três tabelas. Revogar tudo de `anon, authenticated`; devolver `SELECT` em `clinicas/perfis` e `SELECT, INSERT` em `dashboard_arquivos` somente a `authenticated`. Criar policies separadas:

```sql
create policy "conta le o proprio perfil" on public.perfis
for select to authenticated using (user_id = (select auth.uid()));

create policy "conta le a propria clinica" on public.clinicas
for select to authenticated using (exists (
  select 1 from public.perfis p
  where p.user_id = (select auth.uid()) and p.clinica_id = clinicas.id
));

create policy "conta le arquivos da propria clinica" on public.dashboard_arquivos
for select to authenticated using (exists (
  select 1 from public.perfis p
  where p.user_id = (select auth.uid())
    and p.clinica_id = dashboard_arquivos.clinica_id
));

create policy "conta importa na propria clinica" on public.dashboard_arquivos
for insert to authenticated with check (
  importado_por = (select auth.uid()) and exists (
    select 1 from public.perfis p
    where p.user_id = (select auth.uid())
      and p.clinica_id = dashboard_arquivos.clinica_id
  )
);
```

- [ ] **Step 4: Criar a RPC transacional**

Criar `public.importar_dashboard_arquivos(p_arquivos jsonb)` em PL/pgSQL, `security invoker`, `set search_path = ''`. Ela rejeita não-array ou mais de 20 itens; obtém `auth.uid()` e a clínica do perfil; rejeita usuário/perfil ausente; para cada item decodifica `conteudo_base64`, valida nome, linhas, JSON, limite de 20 MB e SHA-256; deriva `clinica_id/importado_por` no servidor; usa `on conflict (clinica_id,id) do nothing`; retorna `jsonb_build_object('novos', ..., 'repetidos', ...)`. Revogar execução de `public, anon` e conceder somente a `authenticated`.

- [ ] **Step 5: Verificar localmente e no Supabase**

Run: `python -m unittest test_migracao_supabase.py -v`

Expected: 2 testes PASS. Depois, executar a migração no SQL Editor do projeto `Dashboard` e confirmar visualmente RLS nas três tabelas, sem policies de `anon`, `UPDATE` ou `DELETE`.

- [ ] **Step 6: Commit**

```bash
git add supabase/migrations/202609110001_auth_rls_clinicas.sql test_migracao_supabase.py
git commit -m "feat: add clinic-isolated Supabase schema"
```

---

### Task 2: Cliente de autenticação e sessão

**Files:**
- Create: `supabase_acesso.py`
- Create: `test_supabase_acesso.py`

**Interfaces:**
- Consumes: `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, nome e senha.
- Produces: `ConfiguracaoSupabase`, `SessaoSupabase`, `email_tecnico`, `entrar`, `restaurar` e `sair`.

- [ ] **Step 1: Escrever testes que falham**

Exigir estes tipos:

```python
class ConfiguracaoSupabase(NamedTuple):
    url: str
    publishable_key: str

@dataclass(frozen=True)
class SessaoSupabase:
    usuario: str
    user_id: str
    clinica_id: str
    clinica_nome: str
    access_token: str
    refresh_token: str
```

E os casos centrais:

```python
def test_email_tecnico_normaliza_nome(self):
    self.assertEqual(email_tecnico(" Clinica.Matriz "), "clinica.matriz@login.dashboard.local")

def test_nome_invalido_e_rejeitado(self):
    for nome in ("", "com espaço", "ácento", "a" * 65):
        with self.subTest(nome=nome), self.assertRaises(ValueError):
            email_tecnico(nome)

def test_login_valida_usuario_e_perfil(self):
    sessao = entrar(self.cliente, "Matriz", "senha")
    self.assertEqual(sessao.usuario, "matriz")
    self.assertEqual(sessao.clinica_id, CLINICA_ID)
    self.cliente.auth.sign_in_with_password.assert_called_once_with({
        "email": "matriz@login.dashboard.local", "password": "senha"
    })
    self.cliente.auth.get_user.assert_called_once()
```

Também testar: configuração exige HTTPS/chave não vazia e rejeita `sb_secret_`; conta sem perfil chama `sign_out` e lança `AcessoNegado`; `restaurar` chama `set_session` e `get_user`, devolvendo tokens renovados; falha do SDK nunca aparece na mensagem; `sair` não propaga erro remoto.

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `python -m unittest test_supabase_acesso.py -v`

Expected: ERROR `ModuleNotFoundError: supabase_acesso`.

- [ ] **Step 3: Implementar `supabase_acesso.py`**

- Validar o nome aparado com `[A-Za-z0-9_.@-]{1,64}`, converter para minúsculas e acrescentar `@login.dashboard.local`.
- Ler configuração de um mapping para permitir testes; criar o cliente real com `supabase.create_client` somente na borda.
- Rejeitar URL sem `https://`, chave vazia, `sb_secret_` e a legacy `service_role`.
- Autenticar com `auth.sign_in_with_password`, validar com `auth.get_user()` e consultar `perfis` mais `clinicas(nome)` para o próprio UUID.
- Exigir exatamente um perfil e igualdade entre `nome_usuario` e o nome normalizado.
- Manter access/refresh tokens somente em `SessaoSupabase`.
- Traduzir erros do SDK para mensagens constantes de `ConfiguracaoInvalida` ou `AcessoNegado`.

- [ ] **Step 4: Rodar os testes e commit**

Run: `python -m unittest test_supabase_acesso.py -v`

Expected: todos PASS.

```bash
git add supabase_acesso.py test_supabase_acesso.py
git commit -m "feat: add Supabase username authentication"
```

---

### Task 3: Repositório do histórico pela Data API

**Files:**
- Modify: `historico.py`
- Modify: `test_historico.py`

**Interfaces:**
- Consumes: cliente Supabase autenticado e dicionários de `preparar_arquivo`.
- Produces: `RepositorioHistorico(cliente)`, `importar(arquivos)`, `listar()` e `carregar(ids)`.

- [ ] **Step 1: Substituir testes SQLAlchemy por um fake encadeável**

Preservar os testes de preparação/consolidação e exigir que:

```python
resultado = repo.importar([arquivo])
self.assertEqual(resultado, {"novos": 1, "repetidos": 0})
nome_rpc, parametros = cliente.chamadas_rpc[0]
self.assertEqual(nome_rpc, "importar_dashboard_arquivos")
payload = parametros["p_arquivos"][0]
self.assertNotIn("clinica_id", payload)
self.assertNotIn("importado_por", payload)
self.assertEqual(base64.b64decode(payload["conteudo_base64"]), b"arquivo-a")
```

Testar também lote acima de 20, resposta RPC malformada, lista vazia, seleção de IDs e `ErroHistorico` sem texto cru do servidor.

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `python -m unittest test_historico.py -v`

Expected: FAIL porque o repositório ainda exige engine/usuário.

- [ ] **Step 3: Implementar o repositório Data API**

- Remover SQLAlchemy, metadata e criação de engine; preservar `preparar_arquivo` e `consolidar_arquivos`.
- Em `importar`, enviar à RPC somente `id`, `nome`, `linhas`, `dados` e `conteudo_base64`; validar retorno como dois inteiros não negativos.
- Em `listar`, selecionar `id,nome,importado_por,importado_em,linhas` e ordenar por `importado_em,id`.
- Em `carregar`, selecionar `id,dados`, filtrar com `.in_("id", ids)` e consolidar; o RLS continua sendo a fronteira de segurança.
- Traduzir falhas do cliente para `ErroHistorico("Operação do histórico indisponível.")` sem parâmetros/URL/resposta.

- [ ] **Step 4: Rodar os testes e commit**

Run: `python -m unittest test_historico.py -v`

Expected: todos PASS.

```bash
git add historico.py test_historico.py
git commit -m "feat: move history repository to Supabase Data API"
```

---

### Task 4: Integrar autenticação e histórico ao Streamlit

**Files:**
- Modify: `app.py`
- Modify: `test_acesso.py`
- Modify: `test_historico_interface.py`

**Interfaces:**
- Consumes: `supabase_acesso` e `RepositorioHistorico(cliente)`.
- Produces: login, restauração, logout, upload e consulta isolados pelo JWT.

- [ ] **Step 1: Reescrever testes AppTest com cliente fake**

Remover hashes e `DASHBOARD_USERS_JSON`. Aplicar patch em `app.obter_cliente_supabase` e cobrir:

- anônimo não vê dashboard;
- credencial inválida produz a mesma mensagem para qualquer nome;
- login remove `login_senha` e guarda `SessaoSupabase`, nunca a senha;
- conta sem perfil, query string e sessão inválida não autenticam;
- restauração chama validação remota e substitui tokens renovados;
- logout chama `sair`, limpa estado e cache;
- sessões AppTest distintas são independentes;
- nenhuma mensagem contém tokens, URL ou resposta remota.

No teste de histórico, usar um fake Data API e confirmar dois arquivos, filtro anual, seleção vazia, exibição apenas textual da clínica e ausência de seletor para `clinica_id`.

- [ ] **Step 2: Rodar e confirmar a falha**

Run: `python -m unittest test_acesso.py test_historico_interface.py -v`

Expected: FAIL porque `app.py` ainda usa autenticação local e `DATABASE_URL`.

- [ ] **Step 3: Implementar a autenticação na UI**

- Remover imports/configuração de JSON, SQLite, hashes e `DASHBOARD_AUTH_DIR`.
- Ler `SUPABASE_URL`/`SUPABASE_PUBLISHABLE_KEY` primeiro do ambiente e depois de chaves escalares de `st.secrets`.
- Criar um cliente por sessão, nunca com `st.cache_resource`, evitando compartilhar tokens entre usuários.
- Em `autenticar`, chamar `entrar`, apagar imediatamente `login_senha` e guardar a sessão.
- Antes da área autenticada, chamar `restaurar`, atualizar tokens e negar acesso em falha.
- Preservar limites locais de oito horas totais e 30 minutos de inatividade.
- Usar a mensagem genérica `Acesso não autorizado. Confira usuário e senha.`.
- Em logout, chamar a saída remota, limpar todas as chaves e `carregar_dados`.

- [ ] **Step 4: Implementar o histórico na UI**

- Remover `conectar_historico` e toda leitura de `DATABASE_URL`.
- Construir `RepositorioHistorico` com o cliente autenticado da execução atual.
- Chamar `repo.importar(preparados)` sem aceitar clínica ou usuário do formulário.
- Mostrar `sessao.clinica_nome` apenas como texto.
- Preservar limites de 20 MB por arquivo, 20 arquivos e 100 MB por lote.
- Sanitizar mensagens de conexão/importação.

- [ ] **Step 5: Rodar regressão e commit**

Run: `python -m unittest test_acesso.py test_historico_interface.py test_leitura.py test_processamento.py -v`

Expected: todos PASS.

```bash
git add app.py test_acesso.py test_historico_interface.py
git commit -m "feat: connect Streamlit to clinic-scoped Supabase"
```

---

### Task 5: Dependências e remoção do legado

**Files:**
- Modify: `requirements.txt`
- Modify: `.gitignore`
- Delete: `seguranca.py`
- Delete: `gerenciar_usuarios.py`
- Delete: `test_seguranca.py`

**Interfaces:**
- Consumes: módulos implementados nas Tasks 2–4.
- Produces: instalação mínima, sem dois sistemas de autenticação concorrentes.

- [ ] **Step 1: Fixar a dependência oficial**

Verificar uma versão de `supabase` compatível com Python 3.14 no ambiente, adicioná-la com versão exata a `requirements.txt` e remover `SQLAlchemy`/`psycopg[binary]` após confirmar ausência de imports.

Run: `grep -RInE 'sqlalchemy|psycopg|DASHBOARD_USERS_JSON|DATABASE_URL|from seguranca|import seguranca' --include='*.py' .`

Expected: nenhuma ocorrência em código Python.

- [ ] **Step 2: Remover arquivos obsoletos e proteger configuração**

Remover `seguranca.py`, `gerenciar_usuarios.py` e `test_seguranca.py`. Manter `.auth/`, `.env`, `.env.*` e `.streamlit/secrets.toml` no `.gitignore`; adicionar `.supabase/` para estado local da CLI.

- [ ] **Step 3: Instalar e testar**

Run: `python -m pip install -r requirements.txt`

Run: `python -m unittest discover -v`

Expected: toda a suíte PASS.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt .gitignore
git rm seguranca.py gerenciar_usuarios.py test_seguranca.py
git commit -m "refactor: remove local authentication backend"
```

---

### Task 6: Documentação e teste real de isolamento

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: projeto Supabase `Dashboard` e migração da Task 1.
- Produces: roteiro administrativo reproduzível sem secret key.

- [ ] **Step 1: Documentar a configuração do Supabase**

Registrar no README:

1. Executar `supabase/migrations/202609110001_auth_rls_clinicas.sql` no SQL Editor.
2. Em **Authentication → Users**, criar `<nome-normalizado>@login.dashboard.local`, senha forte e confirmação administrativa; desabilitar cadastro público.
3. Copiar o UUID do usuário e cadastrar clínica/perfil em transação:

```sql
begin;
insert into public.clinicas (nome) values ('NOME DA CLÍNICA') returning id;
insert into public.perfis (user_id, clinica_id, nome_usuario)
values ('UUID DO USUÁRIO', 'UUID DA CLÍNICA', 'nome-normalizado');
commit;
```

4. Em **Project Settings → API**, copiar somente Project URL e Publishable key.
5. Configurar fora do Git:

```toml
SUPABASE_URL = "https://SEU-PROJETO.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "SUA_CHAVE_PUBLICAVEL"
```

6. Rodar `python -m streamlit run app.py` com planilha fictícia.
7. Revogar acesso banindo/removendo a conta; trocar associação alterando `perfis` e exigindo novo login.

Explicar que a publishable key não concede acesso sem JWT/policy, enquanto secret/service-role ignora RLS e nunca pertence ao app. Incluir links oficiais de RLS, chaves, login Python e `set_session`.

- [ ] **Step 2: Verificar segredos e formatação**

Run: `git diff --check`

Expected: nenhuma saída.

Run: `git grep -InE 'sb_secret_|postgres(ql)?://[^ ]+:[^ ]+@' -- ':!docs/superpowers/plans/*'`

Expected: nenhuma credencial real.

- [ ] **Step 3: Executar teste RLS real com dados fictícios**

Criar duas clínicas e duas contas de teste. Com a publishable key e o token de cada conta, comprovar: A lê/importa A; A não lê/insere B mesmo adulterando payload; `anon` não lê/grava; conta sem perfil não acessa; `UPDATE/DELETE` são negados; o mesmo hash é aceito em clínicas distintas e deduplicado dentro da mesma clínica. Registrar somente resultados, nunca tokens. Remover depois somente os UUIDs fictícios identificados.

- [ ] **Step 4: Verificação final e commit**

Run: `python -m unittest discover -v`

Expected: zero falhas/erros.

Confirmar também que o app sem configuração falha fechado, que nenhum cliente autenticado está em cache global e que `anon` não tem acesso no painel do Supabase.

```bash
git add README.md
git commit -m "docs: document secure Supabase setup"
```
