# Escopo do produto

## Objetivo

Permitir que o dono registre entradas, gastos, transferências e investimentos por conversa, acompanhe carteiras separadas ou somadas e consulte o saldo atual e futuro sem depender do painel.

## Experiência acordada

- O canal inicial é um bot do Telegram, disponível continuamente. O WhatsApp é uma migração futura, sem trocar o núcleo financeiro.
- O GPT é o núcleo da conversa: interpreta pedidos, escolhe ferramentas, responde perguntas e pode produzir análises ou hipóteses temporárias.
- O painel web é opcional e somente para consulta. Lançamentos e correções acontecem pela conversa.
- Texto e voz com valor, data e carteira claros podem gerar lançamentos diretamente. Se faltar informação, o agente pergunta.
- Prints de extratos, comprovantes e PDFs são lidos como propostas. O dono revisa uma lista, corrige ou exclui itens e confirma o lote.
- O agente sugere correspondências com lançamentos existentes, inclusive previstos, e sinaliza possíveis duplicatas antes de importar.
- Categorias de gastos são atribuídas automaticamente; o dono pode corrigi-las. Não há limites de gasto por categoria.
- O agente envia lembretes configuráveis, alerta sobre saldo futuro insuficiente e produz resumo mensal.

## Carteiras e planejamento

- Cada conta bancária ou saldo em dinheiro pode ser uma carteira. A visão consolidada soma somente as carteiras selecionadas do dono.
- Transferências entre carteiras próprias não são receita nem gasto.
- Investimentos são acompanhados em área separada, com aportes, resgates e valor atual informado pelo dono. Seu valor não entra no saldo disponível das carteiras.
- Entradas e saídas futuras, como salário e contas, podem ser únicas ou recorrentes, com valor fixo ou estimado.
- Um evento programado só altera o saldo atual quando o dono confirma que ocorreu. A previsão considera os eventos ainda pendentes.
- A resposta a “quanto tenho?” informa saldo atual e previsão até o fim do mês; qualquer outra data pode ser solicitada.
- O uso planejado é de débito. Não há módulo de compras ou faturas de cartão. Uma obrigação antiga, se existir, é cadastrada como despesa futura da carteira que fará o pagamento.
- A moeda inicial é BRL; datas financeiras seguem o calendário de America/Sao_Paulo.

## Critérios de aceitação do escopo inicial completo

As fases 0 a 3 do [plano de implementação](implementation-plan.md) formam a primeira versão utilizável. O uso de dados reais começa apenas após a fase 4. Os critérios abaixo abrangem também as ampliações das fases 5 a 7; o painel permanece opcional.

1. “Gastei R$ 35 na padaria hoje” cria um gasto realizado na carteira indicada ou pede a carteira se houver ambiguidade.
2. “Meu salário de R$ 5.000 entra todo dia 5” cria uma programação mensal; o saldo atual só muda após confirmação.
3. “Quanto tenho em cada carteira e no total?” responde com valores reconciliáveis com o histórico.
4. “Quanto terei no fim do mês?” inclui apenas programações pendentes até a data consultada e identifica a previsão como tal.
5. Um print com itens repetidos ou previstos gera aviso de duplicidade ou sugestão de vínculo antes da confirmação do lote.
6. Um aporte reduz a carteira de origem e atualiza o acompanhamento de investimentos sem aumentar os gastos de consumo.
7. Uma análise hipotética pode usar os dados existentes, mas não altera saldos ou programações.

## Fora do escopo inicial

Conexão bancária automática, cartões de crédito, orçamentos por categoria, recomendações de investimentos e operação pelo WhatsApp. Há uma exportação JSON inicial; hospedagem, política de cópias de segurança e retenção dos registros serão definidas antes da operação em produção.
