# Fluxos do agente

## Contrato entre GPT e ferramentas

O GPT é o núcleo da interação: entende a intenção, consulta dados e solicita operações. Cada ferramenta recebe argumentos estruturados, identifica o dono pelo contexto autenticado e devolve resultado ou erro claro. O servidor decide se a operação é válida; o modelo nunca monta SQL nem altera saldos diretamente.

Ferramentas candidatas para o primeiro produto:

| Grupo | Operações |
| --- | --- |
| Consulta | Listar carteiras e movimentos; obter saldo atual, consolidado e previsto; consultar programações e investimentos. |
| Registro | Criar entrada ou despesa; criar transferência; registrar aporte e resgate; corrigir ou estornar movimento. |
| Planejamento | Criar, editar ou cancelar programação; listar ocorrências; confirmar ocorrência com valor e data reais. |
| Importação | Criar lote de propostas; consultar correspondências e duplicatas; editar ou descartar proposta; confirmar itens selecionados. |
| Análise | Obter agregados por período e categoria; simular cenário sem persistir movimentos. |

Os esquemas das ferramentas estão definidos em `finance_agent/agent.py`. Uma resposta do GPT não equivale a uma gravação: somente o retorno de uma ferramenta de escrita bem-sucedida confirma o lançamento. Quando a mensagem traz um valor explícito em `R$`, o executor recusa um valor diferente enviado pela ferramenta.
Confirmações de gravação e respostas diretas de saldo e previsão são montadas pelo servidor com os resultados das ferramentas. Nas demais respostas, valores em reais citados pelo GPT precisam aparecer nos dados consultados. Se uma mensagem trouxer valores diferentes para lançamentos ou programações, o agente pede que cada operação seja enviada separadamente para não associar um valor à descrição errada.
Perguntas de consulta recebem apenas ferramentas de leitura. O executor também exige um pedido compatível com cada ferramenta de escrita e recusa hipóteses, negações e perguntas como “posso confirmar?”. Uma resposta curta a uma pergunta imediata de esclarecimento pode completar o pedido anterior; uma confirmação já concluída não autoriza outra gravação.

## Texto e voz

1. O adaptador recebe a mensagem e identifica o dono.
2. Mensagem de voz é transcrita; o texto segue o mesmo fluxo de interpretação.
3. O agente consulta carteiras e categorias quando necessário. Se valor, data, tipo ou carteira estiverem ambíguos, pergunta antes de escrever.
4. Quando os campos estiverem claros, chama a ferramenta adequada. A resposta mostra o que foi registrado e em qual carteira.
5. Correções posteriores usam ferramenta específica e informam o efeito no saldo.

Exemplo: “gastei R$ 35 na padaria hoje” pode criar uma despesa imediatamente quando só há uma carteira ou quando o dono definiu uma carteira padrão. Com várias carteiras e sem padrão, o agente pergunta qual usar. O dono pode dizer “defina Dinheiro como carteira padrão” ou “remova a carteira padrão”. Se citar uma carteira na mensagem, o agente não pode escolher outra automaticamente.

## Prints, comprovantes e PDFs

1. O anexo é validado e interpretado. A extração propõe data, descrição, valor, direção, carteira e categoria, preservando incertezas.
2. O servidor busca movimentos realizados e ocorrências previstas parecidos. Uma coincidência forte é marcada como possível duplicata; uma programação correspondente é sugerida para confirmação com o valor real.
3. O agente apresenta um lote legível com inclusão, exclusão, edição e vínculo para cada item. Nada é gravado nesta etapa.
4. O dono confirma o lote. A ferramenta valida novamente duplicatas e permissões e grava apenas os itens aprovados em transação.
5. O agente devolve resumo: importados, vinculados a programações, ignorados e itens que ainda exigem atenção.

Duplicidade é uma sugestão baseada em atributos como carteira, data, valor e descrição, nunca uma certeza inferida só pelo GPT. Reenvio do mesmo documento ou da mesma confirmação não pode duplicar movimentos.

A confirmação por conversa deve citar explicitamente os números dos itens ou pedir todos. O servidor compara essa seleção aos IDs enviados pela ferramenta e exige a versão atual do lote. Importar uma possível duplicata ou deixar uma ocorrência correspondente sem vínculo exige pedido explícito. O arquivo original não é persistido na implementação atual; ficam o hash e as propostas.
Se o dono mudar um item previamente vinculado para inclusão separada ou exclusão, o rascunho remove o vínculo anterior. A confirmação do lote volta a conferir se existe uma ocorrência parecida antes de gravar uma inclusão separada.

## Programações e previsões

- Uma regra recorrente gera ocorrências futuras para consulta e lembrete. Geração não significa pagamento.
- O dono pode confirmar uma ocorrência por conversa ou a partir de um documento importado. A confirmação registra valor e data reais e vincula o movimento à ocorrência.
- A resposta “quanto tenho?” apresenta saldo atual e previsão até o fim do mês. Uma data explícita substitui esse horizonte.
- O alerta de saldo insuficiente informa a carteira, a data e os compromissos relevantes; não modifica lançamentos.

## Análises e hipóteses

O agente pode comparar meses, resumir categorias e simular perguntas como “e se eu gastar R$ 300 amanhã?”. A simulação é calculada a partir de dados consultados e não cria movimento ou programação. O executor recusa ferramentas de escrita quando identifica um pedido hipotético. Para transformar uma hipótese em lançamento, o dono precisa pedir o registro em uma mensagem separada.

## Falhas

Se a API da OpenAI, a transcrição ou a leitura de um documento falhar, o agente informa a falha e não registra dados parciais. Se uma chamada de escrita retornar erro, a resposta não declara sucesso. Mensagens recebidas novamente são tratadas com idempotência antes de qualquer nova gravação. Se o processo cair depois de confirmar uma escrita no banco, mas antes de salvar a resposta do webhook, a repetição recupera os comandos confirmados, informa o que já foi gravado e não executa novamente o restante do pedido.

Se a gravação já foi confirmada pelo servidor e a chamada seguinte ao modelo falhar, o servidor responde com os dados da operação gravada. Se a resposta ao Telegram falhar, o evento retém o resultado para nova tentativa sem repetir a escrita.
