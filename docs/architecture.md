# Arquitetura

## Visão geral

O Telegram entrega mensagens ao backend FastAPI. Um adaptador de canal normaliza texto, áudio e documentos. O serviço do agente usa a API da OpenAI para interpretar pedidos e selecionar ferramentas. Um executor valida cada chamada, aplica as regras financeiras e persiste em PostgreSQL. Um processo separado verifica programações e envia lembretes. Um painel de consulta pode ser adicionado depois.

Fluxo principal: Telegram → adaptador → agente GPT → executor de ferramentas → serviços financeiros → PostgreSQL → resposta pelo Telegram.

O futuro adaptador de WhatsApp deve usar o mesmo contrato interno de mensagens, ferramentas e serviços. Nenhuma regra de saldo deve morar no adaptador de canal ou no prompt.

## Componentes

| Componente | Responsabilidade |
| --- | --- |
| FastAPI | Receber webhooks e oferecer uma rota de verificação de saúde. |
| Adaptador Telegram | Validar a origem, identificar o dono, normalizar mensagens e enviar respostas. Biblioteca escolhida: aiogram. |
| Serviço do agente | Manter o contexto necessário, expor esquemas de ferramentas ao GPT e conduzir as chamadas à API da OpenAI. |
| Executor de ferramentas | Validar argumentos e autorização, aplicar idempotência e retornar resultados estruturados. |
| Domínio financeiro | Calcular saldos, transferências, programações, investimentos, categorias e reconciliação. |
| Persistência | PostgreSQL com SQLAlchemy; alterações de esquema com Alembic. |
| Importação | Enviar imagens e PDFs para extração estruturada, propor movimentos e detectar correspondências ou duplicatas. |
| Processo de agenda | Enviar lembretes e resumos sem transformar previsões em movimentos realizados. |
| Painel opcional | Páginas de consulta somente leitura; ainda não implementado. |

## Limites importantes

- O GPT decide qual ferramenta solicitar, mas não recebe acesso direto ao banco ou a SQL livre. Toda gravação passa por validação no servidor.
- Respostas sobre saldos usam valores calculados pelo domínio e retornados por ferramentas, não contas feitas apenas pelo modelo.
- Cada comando de escrita tem uma chave de idempotência. Uma atualização repetida do Telegram ou uma nova tentativa da API não deve duplicar lançamentos.
- Transferências e confirmações de importação gravam todos os efeitos correlatos em uma transação de banco.
- O adaptador nunca escolhe regras financeiras; apenas traduz o canal. Isso preserva a futura migração para WhatsApp.
- A integração aiogram com FastAPI precisa de um adaptador próprio: a documentação do aiogram oferece integração pronta com aiohttp, não com FastAPI.

## Entradas multimodais

- Texto segue diretamente para interpretação do agente.
- Voz recebida em OGG/Opus é remuxada para WebM/Opus antes da transcrição. O usuário vê o lançamento entendido na resposta; dúvidas exigem esclarecimento.
- Imagens e PDFs são enviados à API da OpenAI para extração estruturada e geram propostas. A qualidade de PDFs digitalizados ainda precisa de verificação com documentos de teste.
- O processamento de documento não grava movimentos antes da confirmação do lote.

## Stack escolhida

Python, FastAPI, aiogram, SDK Python da OpenAI, PostgreSQL, SQLAlchemy e Alembic. O processo de agenda também é Python. Modelo e hospedagem são configuráveis; o painel ainda não foi escolhido.

## Referências técnicas

- [Chamadas de ferramentas da OpenAI](https://developers.openai.com/api/docs/guides/function-calling)
- [Entradas de imagem da OpenAI](https://developers.openai.com/api/docs/guides/images-vision)
- [Transcrição de arquivos da OpenAI](https://developers.openai.com/api/docs/guides/speech-to-text)
- [Telegram Bot API](https://core.telegram.org/bots/api)
- [Webhooks do aiogram](https://docs.aiogram.dev/en/latest/dispatcher/webhook.html)
- [Transações do SQLAlchemy](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)
