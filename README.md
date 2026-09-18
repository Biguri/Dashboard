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

O dashboard recebe arquivos `.xlsx` por upload manual e os armazena no histórico do Supabase antes de exibir qualquer dado. O banco associa a importação à clínica autenticada por meio do JWT e das policies RLS. Depois do envio, o usuário pode analisar somente os arquivos recém-enviados ou selecionar arquivos do histórico da própria clínica.

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

