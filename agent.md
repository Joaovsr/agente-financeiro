# Agente Financeiro

Este projeto está em implementação. O produto é um agente financeiro pessoal em português, com GPT como núcleo da conversa e ferramentas controladas pelo servidor para consultar e alterar os registros. A validação local usa respostas simuladas das integrações externas; veja o estado da implementação antes de considerar o produto pronto para uso real.

## Leia antes de implementar

1. [Escopo do produto](docs/product-scope.md): comportamento acordado com o usuário.
2. [Arquitetura](docs/architecture.md): componentes, integrações e limites de responsabilidade.
3. [Modelo financeiro](docs/financial-domain.md): entidades, saldos e regras de cálculo.
4. [Fluxos do agente](docs/agent-workflows.md): ferramentas, confirmações e importação.
5. [Segurança](docs/security.md): acesso e validação dos dados.
6. [Decisões](docs/decisions.md): escolhas confirmadas e pontos adiados.
7. [Plano de implementação](docs/implementation-plan.md): sequência sugerida e critérios de entrega.
8. [Estado da implementação](docs/implementation-status.md): evidências locais e validações ainda pendentes.

## Regras essenciais

- O GPT pode solicitar ferramentas; somente o servidor valida e persiste mudanças.
- Saldo atual usa apenas movimentos realizados. Movimentos programados entram apenas na previsão até confirmação.
- Investimentos ficam fora do saldo consolidado das carteiras. Aportes não são gastos de consumo.
- Prints, comprovantes e PDFs geram propostas; o dono revisa antes da gravação. Texto e voz claros podem ser lançados diretamente.
- Todas as consultas e gravações pertencem ao dono autenticado. Valores monetários usam precisão decimal e BRL.
- Análises e hipóteses conversacionais não criam registros financeiros sem uma chamada explícita de ferramenta.

Ao mudar uma regra de produto, atualize primeiro os documentos relacionados. Hospedagem, política de cópias de segurança e retenção para uso real ainda precisam de decisão. Há exportação JSON e um modelo padrão configurável; ambos exigem validação no ambiente escolhido para o piloto.
