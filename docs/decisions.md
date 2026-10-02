# Decisões registradas

Este arquivo distingue escolhas do dono de decisões técnicas propostas. Ele não fixa versões, preços ou serviços de hospedagem.

## Produto confirmado

| Tema | Decisão |
| --- | --- |
| Interface | Agente por Telegram primeiro; WhatsApp depois; painel web opcional e somente leitura. |
| Núcleo | GPT via API da OpenAI conduz a conversa e solicita ferramentas; o servidor valida e executa. |
| Propriedade | Somente o dono acessa suas carteiras e informações. |
| Moeda | Apenas BRL inicialmente. |
| Carteiras | Saldos individuais e consolidados; transferências próprias neutras no consolidado. |
| Investimentos | Aportes, resgates e valor atual em área separada, fora do saldo disponível. |
| Pagamentos | Uso de débito; sem módulo de cartão. Obrigação antiga pode ser despesa futura no débito. |
| Programações | Entradas e saídas únicas ou recorrentes, fixas ou estimadas; confirmação para virar realizadas. |
| Previsão | Mostrar saldo atual e previsto até o fim do mês por padrão, ou até data solicitada. |
| Texto e voz | Lançar diretamente quando claros; perguntar quando houver ambiguidade. |
| Documentos | Prints, comprovantes e PDFs geram propostas em lote; dono revisa e confirma. |
| Conciliação | Sugerir correspondência com previsto ou realizado e avisar sobre duplicatas. |
| Categorias | Classificação automática e corrigível; sem limites por categoria. |
| Avisos | Lembretes configuráveis, alerta de saldo futuro insuficiente e resumo mensal. |
| Hipóteses | Análises e simulações não são persistidas como dados financeiros. |

## Stack proposta e aceita

Python, FastAPI, aiogram, SDK Python da OpenAI, PostgreSQL, SQLAlchemy e Alembic. Um processo Python separado trata a agenda. A integração entre aiogram e FastAPI está no adaptador do Telegram. O painel opcional ainda não tem stack definida.

## Adiado por decisão do dono

- Escolha de hospedagem e serviço gerenciado.
- Política operacional de cópia de segurança, restauração e retenção. Já existe exportação JSON; ela não substitui um backup testado do PostgreSQL.
- Prazo de retenção dos prints e documentos originais.

## Detalhes a escolher na implementação

- Modelo específico da OpenAI, versões das bibliotecas e estratégia de controle de custo.
- Autenticação e implementação do painel opcional.
- Serviço de agenda e forma de armazenar anexos, respeitando as decisões operacionais ainda abertas.
- Regras de confiança para sugerir duplicatas e correspondências, avaliadas com exemplos reais e sem gravação automática de documentos.

## Padrões técnicos provisórios da implementação

- Ocorrências vencidas ficam visíveis separadamente e não são somadas à previsão futura.
- Uma regra mensal com dia inexistente no mês usa o último dia daquele mês.
- O primeiro fluxo de documentos não guarda o arquivo original; mantém hash, propostas e resultado da confirmação. A política definitiva de retenção continua pendente.
- Avisos ficam desativados até o dono configurá-los. Um envio com resultado incerto exige revisão antes de nova tentativa.
