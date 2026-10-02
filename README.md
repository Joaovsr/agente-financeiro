# Agente Financeiro

Implementação em Python de um agente financeiro pessoal para Telegram. O GPT interpreta pedidos e chama ferramentas; saldos, autorização e gravações são calculados e validados pelo servidor. O bot aceita texto, voz, imagens e PDFs. Documentos geram propostas revisáveis antes de qualquer lançamento.

Consulte o [estado da implementação](docs/implementation-status.md) para distinguir o que já passou nos testes locais do que ainda precisa ser validado com serviços reais.

## Desenvolvimento local

Requer Python 3.12 e, para uso com dados reais, PostgreSQL. SQLite é útil apenas para desenvolvimento e testes.

1. Instale as dependências: `python -m pip install -e ".[test]"`.
2. Copie `.env.example` para `.env` e preencha credenciais, ID numérico do seu usuário do Telegram e URL do banco. Se não souber o ID, preencha primeiro `TELEGRAM_BOT_TOKEN`, envie uma mensagem privada ao bot e rode `python -m finance_agent.manage discover-telegram-id` antes de registrar o webhook. Nunca publique `.env`. A API, o processo de agenda, a administração e as migrações leem o mesmo arquivo no diretório do projeto; `AGENT_ENV_FILE` permite indicar outro caminho. Antes de usar dados reais, defina `APP_ENV=production`; essa opção exige PostgreSQL, segredo de webhook longo e credenciais preenchidas.
3. Aplique as migrações: `python -m alembic upgrade head`. Antes de registrar um bot para o piloto, rode `python -m finance_agent.manage preflight`; ele confere a configuração de produção, a conexão com o banco e a versão das migrações, sem chamar serviços externos nem mostrar segredos.
4. Execute a API: `python -m uvicorn finance_agent.main:create_app --factory --host 0.0.0.0 --port 8000`.
5. Com a API disponível por HTTPS público, registre o webhook: `python -m finance_agent.manage set-webhook https://seu-dominio`.
6. Execute o processo de avisos separadamente: `python -m finance_agent.scheduler`. Para uma rodada única, use `--once`.

Para testar a conversa com o bot localmente, sem HTTPS público, deixe a API em execução e rode `python -m finance_agent.poller --skip-pending` em outro processo. Esse comando aceita apenas `APP_ENV=development`, ignora mensagens anteriores à inicialização e encaminha as próximas mensagens privadas ao mesmo webhook local em `127.0.0.1:8000`. Use somente um receptor por bot e não registre um webhook do Telegram enquanto estiver usando polling. Se a API usar outra porta, informe `--api-port PORTA`.

O bot só responde em conversa privada ao `TELEGRAM_USER_ID` configurado. O webhook exige o segredo do cabeçalho configurado. A rota `/healthz` indica que a API está no ar; `/readyz` verifica a conexão com o banco e se todas as migrações foram aplicadas. Em produção, o próprio webhook retorna 503 enquanto o esquema não estiver pronto, para permitir nova tentativa do Telegram. Configure o ambiente para encaminhar tráfego ao bot somente após `/readyz` retornar 200.

Quando uma gravação foi confirmada e o processo caiu antes de salvar a resposta ao Telegram, o reenvio do mesmo evento recupera os comandos confirmados e responde com um resumo, sem repetir a gravação. Se o pedido incluía várias operações, confira o histórico antes de solicitar as partes que não aparecem nesse resumo.

## Ambiente Docker descartável

`docker compose -f compose.dev.yaml up --build -d` sobe PostgreSQL, aplica as migrações e inicia uma instância da API e uma da agenda. Use `http://127.0.0.1:8000/readyz` para verificar API e banco. O banco de teste fica em `127.0.0.1:55432`, com nome `finance_test`; ele usa autenticação local simplificada e armazenamento temporário. Esse ambiente traz credenciais fictícias e não deve receber dados financeiros reais nem ser exposto à internet. Para encerrar e descartar os dados, rode `docker compose -f compose.dev.yaml down --volumes`.

Com o ambiente de teste ativo, defina `TEST_POSTGRES_URL=postgresql+psycopg://postgres@127.0.0.1:55432/finance_test` no seu shell e rode `python -m pytest -q tests/test_postgres_integration.py`. Ajuste `AGENT_TEST_API_PORT` ou `AGENT_TEST_DB_PORT` se as portas padrão estiverem ocupadas.

Em redes com uma autoridade certificadora privada, defina `REQUESTS_CA_BUNDLE` para o arquivo PEM de certificados do host e use `docker compose -f compose.dev.yaml -f compose.ca.yaml up --build -d`. O certificado é montado apenas durante o build da imagem.

## Regras implementadas

- Carteiras em BRL com saldo inicial e data de corte; entradas, despesas, transferências, aportes e resgates realizados.
- Com várias carteiras, lançamentos sem carteira usam apenas um padrão escolhido pelo dono; sem padrão, o bot pede a carteira. O dono pode definir ou remover o padrão pela conversa.
- Confirmações de lançamento, saldo e previsão mostram valores retornados pelo servidor. Quando uma mensagem contém valores diferentes para operações distintas, envie cada lançamento ou programação separadamente.
- Saldos individuais e consolidados, programações únicas ou mensais, confirmação com valor real e previsão com pendências vencidas separadas.
- Correção, estorno e reconciliação de saldo auditáveis; idempotência por comando e por evento do Telegram.
- Categorias de despesas corrigíveis, consultas por período e simulações sem gravação. A voz OGG/Opus do Telegram é convertida para WebM/Opus antes da transcrição.
- Importação de JPG, PNG, WEBP ou PDF até 10 MB, com rascunho, edição e confirmação de itens selecionados. O arquivo original não é guardado no banco; somente o hash e as propostas são persistidos.
- Preferências de avisos, lembretes, alerta de saldo negativo e resumo mensal.

## Dados e operação

Exporte os registros em JSON com `python -m finance_agent.manage export caminho/backup.json`. O arquivo inclui dados financeiros, comandos e conversas; guarde-o em local privado. O comando recusa sobrescrever um arquivo existente. Em produção, mantenha também cópias do PostgreSQL e teste a restauração antes de usar o bot com dados reais.

O processo de avisos marca uma tentativa como `uncertain` quando o envio falha. Isso evita repetição automática quando o resultado do Telegram é desconhecido. Liste os casos com `python -m finance_agent.manage list-notifications`; após conferir se a mensagem chegou, autorize uma nova tentativa com `python -m finance_agent.manage retry-notification ID`. Após uma interrupção do processo, confira também `list-notifications --status sending` e use o mesmo comando para autorizar a repetição dos envios sem recibo. Uma tentativa autorizada passa a `retry_pending` e será enviada na próxima rodada da agenda com os avisos habilitados, mesmo que a data original do aviso tenha passado. Uma mensagem do bot também pode ter sido entregue se a conexão cair antes de registrar o recibo, portanto o envio não oferece garantia absoluta de entrega única. No piloto, execute uma instância da API e um processo de agenda.

O modelo padrão é configurável por `OPENAI_MODEL`. A integração usa a Responses API para chamadas de ferramentas e extração estruturada de documentos. Consulte a [documentação oficial da OpenAI sobre function calling](https://developers.openai.com/api/docs/guides/function-calling) e [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

## Verificação

Rode `python -m pytest -q`. Por padrão, os testes usam SQLite e respostas simuladas da API da OpenAI e do Telegram; o teste PostgreSQL é ignorado sem `TEST_POSTGRES_URL`. Esses testes não comprovam conectividade real com OpenAI ou Telegram nem entrega de mensagens. Antes da publicação, execute um piloto com as credenciais e a hospedagem escolhidas.

Para avaliar a escolha de ferramentas com o modelo configurado em `.env`, rode `python -m finance_agent.evals`, `python -m finance_agent.evals_extended` e `python -m finance_agent.evals_edge`. Os três comandos usam apenas dados fictícios em bancos SQLite temporários e fazem chamadas reais à OpenAI. Eles cobrem o fluxo principal, PDF, simulação, entradas, transferências, correções, estornos, conciliação, investimentos, programações, categorias, avisos, resgate e revisão e confirmação de importação.

Para repetir o teste de integração em PostgreSQL, aplique as migrações em um banco descartável cujo nome termine em `_test`, defina `TEST_POSTGRES_URL` com sua URL e rode `python -m pytest -q tests/test_postgres_integration.py`. O teste cria registros fictícios; não aponte essa variável para o banco do piloto. Em 2026-09-24, o ciclo financeiro e as migrações passaram em um banco temporário no Docker; a restauração de `pg_dump`/`pg_restore` foi verificada separadamente em PostgreSQL 16 temporário.
