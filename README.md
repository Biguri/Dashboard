# Dashboard de Agendamentos

Dashboard com login, leitura de Excel, indicadores de cancelamento, filtros por mês e clínica e rankings de pacientes e profissionais.

## Arquivos

- `app.py`: aplicação completa.
- `test_processamento.py`: testes dos cálculos e filtros.
- `seguranca.py`: lista de acesso, hashes, tentativas e sessões.
- `gerenciar_usuarios.py`: cadastro e remoção de usuários pelo terminal.
- `historico.py`: armazenamento de arquivos e registros no PostgreSQL.
- `test_historico.py` e `test_historico_interface.py`: persistência, consolidação e filtros do histórico.
- `test_seguranca.py`, `test_acesso.py` e `test_leitura.py`: testes de segurança, interface e arquivos.
- `requirements.txt`: dependências com as versões validadas localmente.
- `.python-version`: Python 3.14 para o Render.
- `.gitignore`: exclui planilhas, credenciais locais e arquivos temporários do Git.

## Executar localmente

Na pasta Dashboard, com Python 3.14:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Para executar os testes:

```powershell
python -m unittest discover -v
```

## Autorizar usuários

Sem cadastro, ninguém entra. No terminal da pasta Dashboard, execute para cada pessoa autorizada, substituindo `nome_do_usuario`:

```powershell
python gerenciar_usuarios.py nome_do_usuario
```

Digite e confirme a senha quando solicitado; ela não aparece na tela. Use no mínimo 12 caracteres. O arquivo `.auth/usuarios.json` guarda apenas hashes PBKDF2-SHA256 com salt aleatório e 600.000 iterações; ele está excluído do Git. Use contas individuais e compartilhe a senha apenas com seu titular.

Para trocar uma senha ou revogar acesso:

```powershell
python gerenciar_usuarios.py nome_do_usuario --atualizar
python gerenciar_usuarios.py nome_do_usuario --remover
```

Somente quem administra os arquivos do servidor pode alterar a lista. Não há cadastro público. O cadastro autoriza a pessoa a ver todas as clínicas e dados disponíveis na aplicação; filtros não são permissões por clínica.

Em hospedagem, configure a lista também no serviço. A precedência é: variável `DASHBOARD_USERS_JSON`, secrets `[usuarios]`, arquivo local `.auth/usuarios.json`. Uma fonte externa substitui a lista local; não mescla contas.

## Publicar no Streamlit Community Cloud

1. Crie um repositório **privado** no GitHub e envie os arquivos de código e configuração acima, sem as planilhas dos pacientes.
2. Acesse [Streamlit Community Cloud](https://share.streamlit.io), conecte o GitHub e autorize o acesso ao repositório privado.
3. Clique em **Create app**, selecione repositório, branch e `app.py` como arquivo principal.
4. Em **Advanced settings**, selecione Python **3.14**. Em **Secrets**, cadastre os nomes e copie os respectivos hashes gerados em `.auth/usuarios.json` no formato abaixo. Não use a senha em texto nem o marcador de exemplo. Clique em **Deploy**.

   ```toml
   [usuarios]
   nome_do_usuario = "COLE_AQUI_O_HASH_GERADO"
   ```

5. Abra o endereço gerado, faça login e carregue a planilha em **Upload Manual**. Para acesso por link além dos convidados da plataforma, ajuste a visibilidade do app em suas configurações de compartilhamento; o formulário de login da aplicação continuará obrigatório.

As etapas e a seleção de Python estão na [documentação de publicação do Streamlit](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy).

## Publicar no Render

1. No [Render](https://dashboard.render.com), escolha **New → Web Service** e conecte o mesmo repositório privado.
2. Selecione **Python 3** como linguagem. Mantenha a raiz do serviço na pasta que contém `app.py` e `requirements.txt`.
3. Em **Build Command**, use `python -m pip install -r requirements.txt`.
4. Em **Start Command**, use:

```bash
python -m streamlit run app.py --server.address=0.0.0.0 --server.port=$PORT --server.headless=true
```

5. Em **Environment**, crie `DASHBOARD_USERS_JSON` com o conteúdo completo de `.auth/usuarios.json` (nomes e hashes). Escolha o plano e clique em **Create Web Service**. Abra o endereço gerado, faça login e envie a planilha.

O comando acima é para o ambiente Linux do Render. O arquivo `.python-version` define Python 3.14. Consulte [Web Services](https://render.com/docs/web-services) e [versão do Python](https://render.com/docs/python-version).

## Como os dados funcionam na nuvem

Use **Upload Manual** após o login. O upload não grava a planilha na pasta do projeto e não cria uma base compartilhada persistente; cada sessão envia seu arquivo novamente quando necessário.

**Pasta Automática** consulta a pasta de `app.py` no servidor. Ela não acessa Downloads ou OneDrive do seu computador.

**Histórico na nuvem** armazena os arquivos Excel originais, seus registros, o usuário importador e a data de importação em um PostgreSQL externo. O banco é compartilhado pelos usuários autorizados e permanece independente de reinicializações do dashboard. Todos os usuários da lista atual podem importar e consultar esse histórico.

## Configurar o histórico persistente

1. Crie um banco PostgreSQL gerenciado. No Render, use **New → Postgres**, conforme a [documentação de criação e conexão](https://render.com/docs/postgresql-creating-connecting). Escolha armazenamento persistente e configure backups; a retenção depende do serviço e plano contratados.
2. Copie a URL de conexão fornecida pelo banco para a configuração privada da aplicação. No Render, adicione a variável de ambiente `DATABASE_URL`. No Streamlit Community Cloud, adicione a chave no início de **Secrets**, antes de `[usuarios]`:

   ```toml
   DATABASE_URL = "postgresql://USUARIO:SENHA@SERVIDOR:5432/BANCO?sslmode=require"

   [usuarios]
   nome_do_usuario = "HASH_GERADO_NO_CADASTRO"
   ```

   Substitua os marcadores pela configuração real somente no painel privado. Se o dashboard estiver fora da rede do banco, utilize uma conexão externa autorizada pelo provedor. A aplicação exige TLS e não exibe a URL na tela ou nos logs de importação.
3. Atualize as dependências com `python -m pip install -r requirements.txt` e reinicie a aplicação. A conta do banco precisa criar e consultar a tabela `dashboard_arquivos` e inserir registros nela. A tabela é criada no primeiro acesso autenticado ao histórico.
4. Faça login, escolha **Histórico na nuvem**, selecione vários `.xlsx` e clique em **Armazenar arquivos**. Limites: 20 MB por arquivo e 100 MB por lote. Todos os arquivos precisam ter `Data` e `Status`.
5. Selecione os **Arquivos incluídos na análise**, depois o **Ano**, os meses e as clínicas. Para o ano inteiro, mantenha todos os meses selecionados. A evolução pode ser agrupada por dia ou mês, com uma tabela de comparação mensal.

Arquivos com o mesmo conteúdo são reconhecidos mesmo após renomear e não são gravados novamente. O lote é transacional: uma falha impede a gravação parcial. Relatórios originais permanecem armazenados; desmarcar um arquivo altera apenas a análise atual, sem apagá-lo.

A consolidação remove somente linhas idênticas entre arquivos selecionados e preserva repetições internas de um relatório. Como o exportador não fornece ID único do agendamento, status ou outros dados diferentes **não** são mesclados automaticamente. Ao importar um relatório corrigido, desmarque a versão antiga para não somar as duas versões. A tela também avisa sobre agendamentos com paciente, data, horários e profissional iguais.

O histórico representa os relatórios selecionados; não preenche meses que nunca foram importados. Arquivos originais e dados dos pacientes ficam no banco privado, não no repositório Git. Um provedor com dados persistentes e backups é necessário para sustentar esse histórico.

Os testes de persistência usam SQLite temporário para validar transações e leitura após reconexão. O conector de produção aceita somente PostgreSQL. A conexão real e as permissões do provedor precisam ser verificadas após configurar `DATABASE_URL`.

Os filtros afetam cartões, gráficos, rankings e tabela. “Cancelado” conta como paciente; “Cancelado pelo profissional” conta como profissional. A clínica vem de `Criado por`, mantendo grupos próprios para unidades múltiplas ou não informadas.

## Controles e limites verificados

- São permitidas cinco tentativas por usuário. Cinco falhas em uma janela de 15 minutos bloqueiam novas tentativas por **cinco minutos contados da quinta falha**, inclusive em outras sessões do mesmo servidor. Ao terminar o bloqueio, uma nova sequência de cinco tentativas fica disponível; um login bem-sucedido também zera as falhas.
- Sessões expiram após 30 minutos sem interação ou oito horas de duração total. Remover um usuário ou trocar seu hash invalida a sessão na próxima revalidação. A página conectada verifica a sessão a cada 60 segundos, sem renovar a inatividade.
- Ao usar secrets ou variável de ambiente, atualize a lista nessa fonte e reinicie o serviço para aplicar uma revogação. Alterar somente o cadastro local não muda a configuração da nuvem.
- Logout remove estado e cache de dados da sessão. O cache de leitura é individual por sessão.
- CORS e proteção XSRF permanecem ativos; publicação de arquivos estáticos está desativada. Use o endereço HTTPS fornecido pela hospedagem.
- A limitação de tentativas usa `.auth/tentativas.sqlite3`. Em hospedagem com disco efêmero, o histórico se perde ao recriar o serviço; múltiplas instâncias precisam de controle de tentativas compartilhado. `DASHBOARD_AUTH_DIR` pode apontar para uma pasta persistente no servidor.

Os testes automatizados verificam o comportamento local da aplicação. Não constituem um teste de invasão da hospedagem nem garantem proteção contra compartilhamento de senhas, comprometimento do servidor ou cópias já feitas por usuários autorizados. Os arquivos dos pacientes devem permanecer fora do repositório.

Referências: [armazenamento de senhas da OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html) e [secrets do Streamlit](https://docs.streamlit.io/develop/concepts/connections/secrets-management).
