# Plano de implementação

Este plano divide o trabalho em entregas verificáveis. Há uma implementação inicial no repositório; o [estado da implementação](implementation-status.md) mostra as evidências e as pendências de cada fase. As fases 0 a 3 formam a primeira versão utilizável; a fase 4 é obrigatória antes de usar dados financeiros reais. As fases seguintes ampliam o produto sem mudar as regras da base.

## Fase 0 — Fechar contratos financeiros

Documentar as decisões que afetam saldo e duplicidade antes de criar o esquema: data de corte do saldo inicial e lançamentos retroativos; ajuste auditável de saldo; correção e estorno; identificação de tentativas repetidas; vínculo entre lançamento manual e ocorrência prevista; tratamento de ocorrências vencidas; recorrência em dias inexistentes no mês; e carteira padrão. Definir exemplos de entrada e resultado esperado para cada regra.

**Pronto quando:** cada caso acima tem uma regra única, exemplos verificáveis e uma operação responsável no domínio. A ausência de uma decisão de produto deve ficar explícita, em vez de ser resolvida pelo GPT.

## Fase 1 — Núcleo financeiro

Criar esquema PostgreSQL, migrações e serviços para carteiras, entradas, despesas, transferências, correções e consultas de saldo atual. Registrar saldo inicial com data de corte, manter trilha de alterações e aplicar autorização do dono em todas as consultas e escritas. Transferências e correções com múltiplos efeitos usam transações. Persistir chave e resultado de operações idempotentes.

**Pronto quando:** saldos individuais e consolidados são reproduzíveis pelo histórico; operações repetidas retornam o resultado anterior sem duplicar registros; duas operações distintas de mesmo valor continuam possíveis; e correções explicam o saldo anterior e o novo.

## Fase 2 — Conversa por texto no Telegram

Integrar webhook, identificação do dono, restrição a conversas privadas, serviço do agente GPT e ferramentas de consulta e escrita do núcleo. Registrar entradas, despesas, transferências e correções por texto; pedir valor, data ou carteira quando houver ambiguidade. Toda resposta de sucesso depende do retorno da ferramenta, não da interpretação do modelo.

**Pronto quando:** o dono consegue lançar e consultar movimentos e saldos por conversa; reenvio de webhook ou tentativa da API não duplica lançamentos; uma falha depois da gravação permite recuperar o resultado; e o bot não expõe dados em grupos.

## Fase 3 — Programações e previsão

Implementar programações únicas e recorrentes, geração idempotente de ocorrências, confirmação com valor e data reais, cancelamento e edição. Ao registrar um movimento compatível com uma ocorrência, oferecer ou exigir o vínculo conforme a regra da fase 0. Calcular previsão por carteira e consolidado para uma data, distinguindo pendências futuras das vencidas. Adicionar consultas e comandos de planejamento ao Telegram.

**Pronto quando:** “quanto tenho?” mostra saldo atual e previsão até o fim do mês; confirmar um salário move seu efeito da previsão para o realizado uma única vez; ocorrências vencidas e meses curtos seguem regras documentadas; e o histórico reconcilia com as consultas. Este é o marco da **primeira versão utilizável**, ainda sem dados reais até a fase 4.

## Fase 4 — Preparação para uso real

Escolher hospedagem e configurar segredos, monitoramento e controle de custos. Definir exportação, cópias de segurança e restauração; testar a recuperação dos dados. Verificar versões e limites atuais das integrações antes da publicação. Definir a política de retenção de anexos antes de habilitar importação de documentos.

**Pronto quando:** é possível operar o bot continuamente, recuperar dados de uma cópia, exportar o histórico e investigar falhas sem expor dados financeiros em logs. Só então iniciar um piloto com dados reais.

## Fase 5 — Investimentos, voz e análises

Adicionar aportes, resgates e avaliação manual de investimentos, mantendo seu valor fora do saldo das carteiras e os aportes fora dos gastos de consumo. Transcrever voz e usar as mesmas ferramentas e regras do texto. Implementar agregados e simulações sem gravação financeira.

**Pronto quando:** aporte e resgate têm efeitos reconciliáveis; uma transcrição ambígua pede esclarecimento; e uma hipótese não altera movimentos, programações ou saldos.

## Fase 6 — Importação assistida

Aceitar prints, comprovantes e PDFs; extrair propostas, mostrar incertezas, buscar correspondências com movimentos e ocorrências e permitir revisão item a item. Confirmar somente uma versão identificada do lote, revalidando propriedade e duplicidade dentro da transação. Aplicar a política de retenção definida na fase 4.

**Pronto quando:** reenviar um arquivo ou confirmação não duplica movimentos; itens incertos não são gravados sem revisão; e uma proposta pode confirmar uma ocorrência pelo valor real sem manter seu efeito na previsão.

## Fase 7 — Avisos e consultas adicionais

Adicionar lembretes configuráveis, alerta de saldo futuro insuficiente e resumo mensal. O processo de agenda deve registrar tentativas e resultado de envio para reduzir notificações repetidas e permitir recuperação após falhas. O painel autenticado e somente leitura é uma entrega **opcional** desta fase; se criado, deve consultar os mesmos serviços financeiros do agente.

**Pronto quando:** avisos usam as regras de previsão e as preferências do dono; falhas de envio podem ser investigadas e tentadas novamente; e, caso exista painel, seus saldos coincidem com os da conversa.

## Depois do escopo inicial

A migração para WhatsApp usa o contrato de canal já criado. Conexão bancária automática, cartões de crédito e orçamentos por categoria permanecem fora do escopo inicial.
