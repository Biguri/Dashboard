# Dashboard de Agendamentos

Aplicação Streamlit para analisar relatórios Excel de agendamentos, com autenticação pelo Supabase e histórico persistente isolado por clínica por meio de Row Level Security (RLS).

## Executar localmente

Requisitos: Python 3.14 e um projeto Supabase configurado conforme as seções seguintes.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

No Linux ou macOS, ative o ambiente com `source .venv/bin/activate`.

O dashboard aceita `.xlsx` por upload manual, pela pasta de `app.py` ou pelo histórico no Supabase. O upload manual permanece apenas na sessão atual. O histórico armazena o arquivo original e os dados processáveis no banco da clínica autenticada.

## 1. Criar a estrutura no Supabase

No projeto **Dashboard**, abra **SQL Editor**, crie uma consulta, copie todo o conteúdo de [`supabase/migrations/202609110001_auth_rls_clinicas.sql`](supabase/migrations/202609110001_auth_rls_clinicas.sql) e execute uma vez.

A migração cria:

- `clinicas`, com uma linha por clínica;
- `perfis`, ligando uma conta do Supabase Auth a uma clínica;
- `dashboard_arquivos`, isolada por clínica;
- uma função transacional para importar lotes;
- grants mínimos e policies RLS para usuários autenticados.

Requisições anônimas não recebem acesso. O aplicativo não recebe permissão para editar ou excluir dados. A clínica e o importador são derivados no banco a partir de `auth.uid()`; campos enviados pelo cliente não podem trocar a clínica.

## 2. Desabilitar cadastro público

Nas configurações de **Authentication**, desabilite novos cadastros públicos. As contas devem ser criadas somente por quem administra o projeto.

Não use um e-mail pessoal. Para manter o login visível apenas por nome, cada conta usa internamente:

```text
nome-normalizado@login.dashboard.local
```

O nome aceita apenas letras sem acento, números, `_`, `.` e `-`, com até 64 caracteres. Ele é convertido para minúsculas. Exemplo: o login `Clinica.Matriz` corresponde internamente a `clinica.matriz@login.dashboard.local`.

## 3. Criar a conta e associar a clínica

1. Em **Authentication → Users**, crie o usuário com o e-mail técnico, uma senha forte e confirmação administrativa.
2. Copie o UUID do usuário criado.
3. No SQL Editor, execute a transação abaixo depois de substituir os três marcadores. O `nome-normalizado` deve ser exatamente a parte anterior a `@login.dashboard.local`.

```sql
begin;

with nova_clinica as (
    insert into public.clinicas (nome)
    values ('NOME DA CLÍNICA')
    returning id
)
insert into public.perfis (user_id, clinica_id, nome_usuario)
select 'UUID DO USUÁRIO'::uuid, id, 'nome-normalizado'
from nova_clinica;

commit;
```

Nesta versão, a restrição do banco permite uma conta por clínica e uma clínica por conta. Repita o processo para cada clínica.

Como o endereço é técnico, a recuperação automática por e-mail não é usada. A redefinição de senha é administrativa no painel do Supabase.

## 4. Configurar somente as chaves públicas

Em **Project Settings → API**, copie:

- **Project URL**;
- **Publishable key**, cujo prefixo atual é `sb_publishable_`.

Nunca copie para o aplicativo a **Secret key**, a chave legada `service_role` ou a senha direta do PostgreSQL. Essas credenciais ignoram o RLS.

Para desenvolvimento local, crie `.streamlit/secrets.toml` — o arquivo já está ignorado pelo Git:

```toml
SUPABASE_URL = "https://SEU-PROJETO.supabase.co"
SUPABASE_PUBLISHABLE_KEY = "SUA_CHAVE_PUBLICAVEL"
```

Também é possível usar variáveis de ambiente com os mesmos nomes. Variáveis de ambiente têm precedência sobre `st.secrets`.

## 5. Validar antes de usar dados reais

Crie duas clínicas e duas contas fictícias. Use somente planilhas fictícias e confirme:

- a conta A importa e lista dados da clínica A;
- a conta B não vê os arquivos da clínica A;
- uma conta sem perfil não entra;
- logout exige nova autenticação;
- uma requisição sem login não lê nem grava arquivos.

Depois da validação, remova somente as contas, clínicas e arquivos fictícios cujos UUIDs você identificou. Não teste inicialmente com dados de pacientes.

## Publicar

No Streamlit Community Cloud ou Render, configure `SUPABASE_URL` e `SUPABASE_PUBLISHABLE_KEY` no painel privado do serviço. Não crie `.env` ou `secrets.toml` versionado.

Comando de inicialização no Render:

```bash
python -m streamlit run app.py --server.address=0.0.0.0 --server.port=$PORT --server.headless=true
```

O Python é definido em `.python-version`. CORS e proteção XSRF permanecem ativos e a publicação de arquivos estáticos está desativada.

## Comportamento e limites

- Sessões expiram após 30 minutos sem interação ou oito horas no total.
- O token é validado novamente no Supabase durante o uso; conta removida, banida ou sem perfil perde acesso.
- Logout remove o estado e o cache individual da sessão.
- Cada arquivo deve ser `.xlsx`, ter até 20 MB e conter `Data` e `Status`.
- Cada importação aceita até 20 arquivos e 100 MB no total.
- O lote é transacional: uma falha impede gravação parcial.
- O mesmo conteúdo é deduplicado dentro da clínica, mas pode existir em clínicas diferentes.
- Todos os registros de um relatório pertencem à clínica da conta que o importou; valores da coluna `Criado por` são apenas dimensões analíticas, não autorização.

## Testes

```bash
python -m unittest discover -v
```

Os testes locais usam doubles para a API e não precisam de tokens. O isolamento RLS definitivo deve ser verificado também no projeto Supabase de desenvolvimento com duas contas fictícias.

## Referências oficiais

- [Row Level Security](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Chaves da API](https://supabase.com/docs/guides/getting-started/api-keys)
- [Login por senha no cliente Python](https://supabase.com/docs/reference/python/auth-signinwithpassword)
- [Restauração de sessão no cliente Python](https://supabase.com/docs/reference/python/auth-setsession)
