# Supabase Auth e isolamento por clínica

## Objetivo

Migrar a autenticação própria e o acesso PostgreSQL direto do dashboard para Supabase Auth e a Data API do Supabase. Cada conta pertence a exatamente uma clínica e só pode importar ou consultar dados dessa clínica. Não haverá cadastro público nem credenciais administrativas no aplicativo ou no repositório.

## Decisões

- A interface continuará solicitando nome de usuário e senha.
- Internamente, o nome normalizado será convertido em um identificador de e-mail técnico determinístico aceito pelo Supabase Auth. Esse identificador não será exibido ao usuário.
- Contas serão criadas pelo administrador no painel do Supabase, com e-mail confirmado administrativamente, e associadas manualmente a uma clínica.
- Cada conta pertencerá a uma única clínica e cada clínica terá uma única conta nesta primeira versão.
- O Streamlit usará apenas `SUPABASE_URL` e `SUPABASE_PUBLISHABLE_KEY`, configuradas fora do Git.
- Consultas de aplicação usarão o token do usuário autenticado. Não haverá secret key, `service_role` ou senha direta do PostgreSQL no processo do dashboard.
- Os arquivos Excel continuarão armazenados na tabela, evitando incluir Supabase Storage nesta migração.
- O aplicativo continuará permitindo somente leitura e importação; edição e exclusão não serão concedidas.

## Modelo de dados

### `public.clinicas`

- `id uuid primary key default gen_random_uuid()`
- `nome text not null unique`
- `criada_em timestamptz not null default now()`

### `public.perfis`

- `user_id uuid primary key references auth.users(id) on delete cascade`
- `clinica_id uuid not null unique references public.clinicas(id)`
- `nome_usuario text not null unique`
- `criado_em timestamptz not null default now()`

A restrição única em `clinica_id` formaliza a decisão de uma conta por clínica. `nome_usuario` deve armazenar a forma canônica usada para gerar o e-mail técnico, permitindo auditoria administrativa sem expor e-mails reais.

### `public.dashboard_arquivos`

Mantém `id`, `nome`, `importado_em`, `linhas`, `conteudo` e `dados`. A coluna textual `usuario` será substituída por:

- `clinica_id uuid not null references public.clinicas(id)`
- `importado_por uuid not null references auth.users(id)`

O identificador de conteúdo deve ser único dentro da clínica, não globalmente. A chave apropriada será composta por `(clinica_id, id)`, para que clínicas diferentes possam importar o mesmo relatório sem observar a existência uma da outra.

## Autenticação

O formulário recebe nome e senha. O nome é validado pela mesma regra restritiva já usada pelo projeto, normalizado para minúsculas e convertido em e-mail técnico em um domínio reservado da aplicação. A criação da conta no painel deve usar exatamente a mesma conversão documentada.

O cliente Supabase autentica com senha e mantém o access token e o refresh token somente em `st.session_state`. O token do usuário acompanha todas as chamadas à Data API. Logout chama o encerramento de sessão do Supabase e remove o estado e os caches locais. Falhas de login usam mensagem genérica. Conta válida sem linha em `perfis` falha de forma fechada e não acessa dados.

Não haverá recuperação automática de senha nesta versão, pois os endereços são técnicos. Redefinições serão feitas pelo administrador no painel do Supabase.

## Autorização e RLS

RLS será habilitado nas três tabelas expostas. As permissões de `anon` serão revogadas. O papel `authenticated` receberá somente as permissões necessárias antes da avaliação das políticas.

Uma política de `clinicas` permitirá ao usuário autenticado consultar somente a clínica referenciada por seu próprio perfil. A aplicação não poderá criar, editar ou excluir clínicas.

Políticas de `perfis` permitirão ao usuário autenticado consultar exclusivamente sua própria linha, por `user_id = (select auth.uid())`. As tabelas administrativas não poderão ser alteradas pela aplicação.

Em `dashboard_arquivos`, políticas separadas permitirão:

- `SELECT` quando `clinica_id` corresponder à clínica do perfil cujo `user_id` é `auth.uid()`;
- `INSERT` somente quando `clinica_id` corresponder ao mesmo perfil e `importado_por = auth.uid()`.

Não serão criadas políticas de `UPDATE` ou `DELETE`. A ausência de perfil, token inválido, papel anônimo ou clínica divergente resulta em nenhuma linha visível ou em inserção negada. Índices em `perfis(user_id)`, `perfis(clinica_id)` e `dashboard_arquivos(clinica_id, importado_em)` dão suporte às políticas e listagens.

O SQL deve configurar explicitamente grants e policies. A migração também deve impedir que valores fornecidos pelo navegador ou pelo formulário ampliem o escopo: o aplicativo deriva os identificadores da sessão, e o banco os verifica novamente.

## Fluxo de dados

1. O usuário informa nome e senha.
2. O Streamlit converte o nome no identificador técnico e autentica no Supabase Auth.
3. O aplicativo consulta o próprio perfil com o token recebido.
4. Se houver exatamente uma clínica associada, a sessão é iniciada; caso contrário, o acesso é negado.
5. Na importação, o aplicativo prepara o arquivo como hoje e envia `clinica_id` e `importado_por` derivados da sessão.
6. O RLS valida ambos os campos e grava a linha.
7. Listagens e carregamentos passam pela Data API autenticada e retornam somente arquivos da clínica da conta.

## Mudanças no código

- Criar um módulo de integração Supabase responsável por configuração, conversão do nome, autenticação, renovação/encerramento da sessão e criação de cliente autenticado.
- Substituir em `app.py` o cadastro local, a validação PBKDF2 e a conexão `DATABASE_URL` pelo fluxo Supabase.
- Adaptar `historico.py` para um repositório sobre a Data API, preservando a preparação e consolidação local dos DataFrames.
- Adicionar a biblioteca Python oficial do Supabase e remover dependências de conexão direta que deixarem de ser utilizadas.
- Adicionar uma migração SQL versionada com esquema, grants, índices e policies.
- Atualizar o README com criação do projeto, execução da migração, criação de clínica/conta, associação do perfil e configuração das duas variáveis públicas.
- Manter arquivos `.env`, secrets do Streamlit e quaisquer credenciais ignorados pelo Git.

## Tratamento de erros

- Configuração ausente: negar acesso e registrar apenas uma mensagem operacional sem valores de configuração.
- Credenciais inválidas: mostrar mensagem genérica, sem distinguir usuário inexistente de senha incorreta.
- Perfil ausente ou inconsistente: negar acesso e orientar contato com o administrador.
- Token expirado: tentar a renovação suportada pelo cliente; se falhar, limpar a sessão e solicitar novo login.
- Violação de RLS ou falha da Data API: não retornar detalhes internos na interface; registrar somente informação sanitizada.
- Falha durante lote de importação: a interface não deve afirmar sucesso parcial. A importação usará uma função RPC transacional, executada com o JWT do usuário e sujeita às mesmas validações de clínica e identidade.

## Migração e compatibilidade

Como o repositório não contém uma base Supabase existente, a migração cria o novo esquema de forma reproduzível. Se já houver dados em `dashboard_arquivos` no PostgreSQL atual, eles exigirão uma etapa administrativa separada que atribua explicitamente cada registro a uma clínica antes de tornar `clinica_id` obrigatória. Nenhum registro será associado automaticamente com base em texto livre.

O antigo arquivo `.auth/usuarios.json` deixa de ser fonte de autenticação. Hashes PBKDF2 não podem ser convertidos em senhas do Supabase; as contas precisarão receber novas senhas. O banco local de tentativas deixa de controlar o login depois da migração.

## Testes e critérios de aceitação

Testes unitários devem cobrir normalização do nome, configuração ausente, criação e limpeza da sessão, preparação dos dados e respostas de erro sanitizadas. Clientes Supabase serão substituídos por doubles nos testes locais.

Testes de integração contra um projeto Supabase de desenvolvimento devem demonstrar que:

- uma conta autenticada lê e importa dados da própria clínica;
- a conta da clínica A não lê dados da clínica B;
- a conta da clínica A não consegue inserir declarando a clínica B ou outro `importado_por`;
- uma requisição anônima não lê nem grava registros;
- uma conta sem perfil não lê nem grava registros;
- `UPDATE` e `DELETE` são negados;
- logout ou token inválido impede novas operações;
- o mesmo hash de arquivo pode existir em clínicas distintas, mas é deduplicado dentro da mesma clínica;
- nenhum secret ou token é incluído no Git ou exibido em logs e mensagens.

A implementação estará concluída quando a suíte local passar, os testes de isolamento forem executados no ambiente de desenvolvimento e o README permitir reproduzir a configuração sem inserir chaves privadas no repositório.
