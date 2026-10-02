# Modelo financeiro

## Entidades propostas

| Entidade | Papel |
| --- | --- |
| Dono | Identidade que possui todas as carteiras, registros e permissões. |
| Carteira | Conta ou saldo em BRL, com nome, saldo inicial, data inicial e estado ativo. |
| Movimento realizado | Entrada, despesa, transferência, aporte ou resgate confirmado, com valor, data, carteira e origem. |
| Programação | Regra única ou recorrente que gera ocorrências futuras com valor fixo ou estimado. |
| Ocorrência prevista | Instância de uma programação, pendente, confirmada ou cancelada. |
| Investimento | Posição acompanhada fora das carteiras, com aportes, resgates e avaliação manual. |
| Categoria | Classificação automática e corrigível de despesas e entradas. |
| Lote de importação | Documento recebido, propostas extraídas, correspondências e decisão do dono. |
| Notificação | Lembrete ou resumo com agendamento e registro de envio. |

## Saldos

- Saldo atual de uma carteira = saldo inicial + entradas realizadas − despesas realizadas + transferências recebidas − transferências enviadas − aportes + resgates + ajustes de reconciliação. Valores previstos não entram nessa soma.
- Saldo consolidado = soma dos saldos atuais das carteiras selecionadas. O valor dos investimentos é mostrado separadamente.
- Saldo previsto para uma data = saldo atual + efeitos líquidos das ocorrências pendentes com vencimento até essa data. Uma ocorrência vinculada a movimento realizado deixa de entrar na previsão.
- Transferências entre carteiras próprias têm efeito líquido zero no consolidado, inclusive na projeção. Aportes reduzem o saldo das carteiras, mas não compõem o total de gastos de consumo; resgates fazem o movimento inverso.
- O saldo projetado pode ficar negativo; o sistema alerta o dono e mostra quais compromissos contribuíram para isso.
- O saldo inicial vale a partir da data de abertura da carteira. Movimentos retroativos são aceitos somente dessa data em diante; movimento realizado com data futura é recusado.
- Se houver apenas uma carteira ativa, ela pode ser usada quando a mensagem não informa carteira. Com várias carteiras, o dono deve indicar uma ou definir explicitamente uma carteira padrão. Uma nova carteira não se torna padrão automaticamente. O dono pode alterar ou remover essa preferência; sem padrão, o agente pede a carteira antes de gravar.
- Uma reconciliação solicitada pelo dono registra a diferença entre o saldo calculado e o saldo real informado como ajuste auditável. O ajuste não é receita ou gasto de consumo.
- Ocorrências pendentes vencidas aparecem separadas e não entram automaticamente na previsão futura, pois seu pagamento ou recebimento ainda não foi confirmado.
- Uma recorrência mensal em dia inexistente no mês vence no último dia desse mês (por exemplo, dia 31 vence em 28 ou 29 de fevereiro). Editar uma regra cria uma substituta para as datas futuras e preserva as ocorrências anteriores à data da edição, inclusive pendências vencidas ainda não consultadas.

## Regras de integridade

1. Dinheiro é armazenado em tipo decimal, com precisão de centavos, sem ponto flutuante binário.
2. Todo movimento pertence ao dono e referencia uma carteira sua. Transferências referenciam origem e destino distintos, ambos do mesmo dono.
3. Uma transferência é uma única operação lógica. Seus dois efeitos são persistidos atomicamente.
4. Cada ocorrência prevista pode ser confirmada uma única vez e vinculada ao movimento realizado correspondente. Diferenças entre estimativa e valor real ficam registradas.
5. A recorrência gera ocorrências identificáveis; reprocessar a agenda não cria a mesma ocorrência duas vezes.
6. Uma correção preserva histórico suficiente para explicar o saldo anterior e o novo. Exclusão lógica ou estorno é preferível a apagar silenciosamente um movimento realizado.
7. Uma proposta de importação não altera o saldo. Ao confirmar o lote, só os itens selecionados e ainda não registrados viram movimentos.
8. A classificação é automática e corrigível. Mudá-la não altera o valor ou a carteira do movimento.
9. Ao registrar entrada ou despesa manual, o servidor sinaliza uma ocorrência pendente compatível por carteira, tipo e proximidade de data, valor ou descrição. Lançá-la separadamente exige pedido explícito do dono.
10. Uma chave de comando repetida retorna o resultado anterior somente quando os argumentos também são idênticos. Uma chave reutilizada com argumentos diferentes é recusada.

## Datas e exemplos

Datas de lançamento e vencimento seguem America/Sao_Paulo. Horários técnicos de recebimento e auditoria podem ser guardados em UTC. O mês de referência de um resumo segue o calendário local.

Exemplo: com R$ 2.000 atuais, salário previsto de R$ 5.000 e conta prevista de R$ 1.200 até o fim do mês, a resposta é R$ 2.000 atuais e R$ 5.800 previstos. Quando o salário for confirmado, ele passa para o saldo atual e sai da parcela pendente da previsão; o total previsto permanece coerente.
