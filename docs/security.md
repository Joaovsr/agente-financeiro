# Segurança e operação

## Acesso

- O produto inicial é pessoal. Cada carteira e registro pertence ao dono identificado por um ID interno.
- O bot aceita operações somente do ID de usuário do Telegram autorizado. Nome de usuário, nome exibido e conteúdo da mensagem não servem como prova de identidade.
- O bot processa dados financeiros somente em conversa privada com esse usuário.
- Se for criado, o painel exigirá autenticação própria ou sessão vinculada de forma segura ao dono. Seu acesso será somente leitura na fase inicial.
- Qualquer ferramenta de leitura ou escrita aplica o filtro de proprietário no servidor, inclusive ao usar IDs enviados pelo GPT.

## Segredos e integrações

- Tokens do Telegram, credenciais do PostgreSQL e chave da OpenAI ficam no servidor, fora do repositório e do navegador.
- O webhook do Telegram deve validar o token secreto configurado no cabeçalho antes de processar mensagens.
- A integração com a OpenAI recebe apenas o contexto necessário para o pedido. Logs de aplicação não devem registrar chaves, documentos completos ou dados financeiros sem necessidade.
- A futura integração com WhatsApp terá credenciais e validação de webhook próprias, preservando as mesmas regras de autorização.

## Conteúdo não confiável

Extratos, PDFs, imagens, transcrições e mensagens podem conter instruções maliciosas ou incorretas. Seu conteúdo é dado de entrada, não instrução do sistema. O executor rejeita argumentos fora do esquema, carteiras de outro dono, valores inválidos e ferramentas não autorizadas.

Uploads exigem verificação de tipo, tamanho e quantidade antes do envio para processamento. A leitura de documentos nunca executa arquivos anexados. Uma proposta extraída de imagem permanece pendente até a confirmação do dono.

Na implementação atual, o arquivo original não é guardado após a extração; o hash e as propostas permanecem no banco. A política definitiva de retenção e eventual exclusão desses registros ainda precisa ser definida antes da operação em produção.

## Integridade e observabilidade

- Valores são decimais; operações com múltiplos efeitos usam transação de banco.
- Webhooks, confirmações e notificações usam chaves de idempotência para resistir a reenvios.
- O histórico de correções registra quem pediu, quando e o vínculo com o movimento anterior.
- Erros são monitorados sem expor dados sensíveis em logs; falhas de envio de lembrete podem ser tentadas novamente com controle de duplicidade.

## Decisões adiadas

Hospedagem, retenção dos registros e política de cópias de segurança e restauração ainda não foram especificadas pelo dono. Há um comando de exportação JSON, mas prazo de retenção e teste de recuperação precisam ser definidos antes de colocar dados reais em produção.
