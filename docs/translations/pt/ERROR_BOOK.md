---
translation: machine
source_sha256: 5a872f9502c66828af4dfb1e7e687226facc2aaad8cb6544054c90e1fafe8d7d
---

# Livro de erros do VOOL

<!-- GENERATED FILE — do not hand-edit. Regenerate with `python -m tools.generate_error_book`. -->

<!-- GENERATED FILE: the app's own texts for each code, and the words in tools/docs/error_book_words.json. Regenerate with tools/docs/translate_error_book.py, never by hand. -->

Todos os erros que o VOOL pode mostrar a você, gerados a partir do próprio catálogo de erros do app: os códigos, as mensagens e os próximos passos abaixo são exatamente o que o app mostra.

- **Esquema:** `vool.fault.v1`
- **Versão do catálogo:** `1`
- **Resumo do catálogo:** `2d2c27206b99b26ca524806ec42ac41eac2f0d28a110a74f6ac5ef42006f313d`
- **Número de falhas:** 59

Cada código é fixo e nunca é reutilizado com outro significado. Cada código tem a sua própria seção abaixo, cujo título é o próprio código; assim, ao buscar nesta página o código como você o recebeu, você encontra a seção dele, e um link para a seção é o endereço desta página seguido de `#` e do código com hifens no lugar dos sublinhados. Cada seção diz o que aconteceu, o que pode já ter acontecido, o que fazer em seguida e se tentar de novo pode ajudar. Se um código que você recebeu não está neste livro, ele não é desta versão do VOOL: confira qual versão o mostrou, ou informe-o com o código como está escrito.

## Segurança

| Código | Título | Gravidade | O que pode já ter acontecido |
|---|---|---|---|
| [`confinement_refusal`](#confinement-refusal) | Mudança fora do escopo permitido | alta | Nada foi enviado, assinado ou alterado |
| [`credential_failure`](#credential-failure) | Não foi possível ler uma credencial armazenada | alta | Nada foi enviado, assinado ou alterado |
| [`wallet_backup_unavailable`](#wallet-backup-unavailable) | Backup já mostrado uma vez | alta | Nada foi enviado, assinado ou alterado |
| [`wallet_caller_refused`](#wallet-caller-refused) | Não iniciado a partir da própria janela do VOOL | alta | Nada foi enviado, assinado ou alterado |
| [`wallet_card_data_refused`](#wallet-card-data-refused) | Dados de cartão recusados | alta | Nada foi enviado, assinado ou alterado |
| [`wallet_chain_identity_mismatch`](#wallet-chain-identity-mismatch) | Identidade do ponto da cadeia não provada | alta | Uma solicitação chegou a um serviço externo; nenhum pagamento foi feito |
| [`wallet_environment_inactive`](#wallet-environment-inactive) | Ambiente de rede errado | média | Nada foi enviado, assinado ou alterado |
| [`wallet_export_pin_not_accepted`](#wallet-export-pin-not-accepted) | A exportação só é destravada pela autenticação do dispositivo | alta | Nada foi enviado, assinado ou alterado |
| [`wallet_export_refused`](#wallet-export-refused) | Forma antiga de exportar aposentada | alta | Nada foi enviado, assinado ou alterado |
| [`wallet_export_unavailable`](#wallet-export-unavailable) | Exportação indisponível para esta carteira | alta | Nada foi enviado, assinado ou alterado |
| [`wallet_legacy_surface_retired`](#wallet-legacy-surface-retired) | Caminho de dinheiro legado aposentado | alta | Nada foi enviado, assinado ou alterado |
| [`wallet_network_disabled`](#wallet-network-disabled) | Rede não declarada para esta carteira | alta | Nada foi enviado, assinado ou alterado |
| [`wallet_outbound_refused`](#wallet-outbound-refused) | Pedido fora dos pontos aprovados | alta | Nada foi enviado, assinado ou alterado |
| [`wallet_quote_mismatch`](#wallet-quote-mismatch) | A aprovação não corresponde mais à prévia | alta | Nada foi enviado, assinado ou alterado |
| [`wallet_recovery_refused`](#wallet-recovery-refused) | A prova de recuperação não serve para esta carteira | alta | Nada foi enviado, assinado ou alterado |
| [`wallet_signature_invalid`](#wallet-signature-invalid) | A assinatura não correspondeu à chave da carteira | alta | Uma solicitação chegou a um serviço externo; nenhum pagamento foi feito |
| [`wallet_unlock_throttled`](#wallet-unlock-throttled) | Carteira trancada após destravamentos malsucedidos | alta | Nada foi enviado, assinado ou alterado |

## Integridade

| Código | Título | Gravidade | O que pode já ter acontecido |
|---|---|---|---|
| [`evidence_corruption`](#evidence-corruption) | Os registros da resposta falharam numa verificação de consistência | alta | Houve um registro ou uma alteração local |
| [`integrity_verification_failure`](#integrity-verification-failure) | Um registro falhou na verificação à prova de adulteração | crítica | Houve um registro ou uma alteração local |
| [`unsupported_claim`](#unsupported-claim) | Afirmações sem apoio retidas | média | Houve um registro ou uma alteração local |
| [`wallet_duplicate_payment`](#wallet-duplicate-payment) | Pagamento já proposto ou enviado | média | Houve um registro ou uma alteração local |

## Política

| Código | Título | Gravidade | O que pode já ter acontecido |
|---|---|---|---|
| [`evm_pocket_custody_unavailable`](#evm-pocket-custody-unavailable) | Bolso legado só para Solana | baixa | Nada foi enviado, assinado ou alterado |
| [`permission_denied`](#permission-denied) | Permissão negada | média | Nada foi enviado, assinado ou alterado |
| [`wallet_acknowledgement_required`](#wallet-acknowledgement-required) | Confirmação necessária | baixa | Nada foi enviado, assinado ou alterado |
| [`wallet_amount_invalid`](#wallet-amount-invalid) | Valor não exato para esta moeda | baixa | Nada foi enviado, assinado ou alterado |
| [`wallet_approval_rejected`](#wallet-approval-rejected) | Aprovação não aceita | média | Nada foi enviado, assinado ou alterado |
| [`wallet_confirmation_required`](#wallet-confirmation-required) | Confirmação digitada necessária | baixa | Nada foi enviado, assinado ou alterado |
| [`wallet_credential_mismatch`](#wallet-credential-mismatch) | As entradas não conferiram | baixa | Nada foi enviado, assinado ou alterado |
| [`wallet_dependency_unavailable`](#wallet-dependency-unavailable) | Bibliotecas EVM não instaladas | média | Nada foi enviado, assinado ou alterado |
| [`wallet_device_auth_denied`](#wallet-device-auth-denied) | Destravamento pelo dispositivo recusado | baixa | Nada foi enviado, assinado ou alterado |
| [`wallet_disabled`](#wallet-disabled) | A carteira está desligada | baixa | Nada foi enviado, assinado ou alterado |
| [`wallet_insufficient_funds`](#wallet-insufficient-funds) | O saldo não cobre o valor e a taxa | baixa | Uma solicitação chegou a um serviço externo; nenhum pagamento foi feito |
| [`wallet_limit_exceeded`](#wallet-limit-exceeded) | Limite de gasto excedido | média | Nada foi enviado, assinado ou alterado |
| [`wallet_not_found`](#wallet-not-found) | Carteira, proposta ou destino não reconhecido | baixa | Nada foi enviado, assinado ou alterado |
| [`wallet_password_invalid`](#wallet-password-invalid) | Senha da carteira inválida | baixa | Nada foi enviado, assinado ou alterado |
| [`wallet_paykit_unavailable`](#wallet-paykit-unavailable) | Extra pay da Solana não instalado | média | Nada foi enviado, assinado ou alterado |
| [`wallet_payment_channel_off`](#wallet-payment-channel-off) | Canal de pagamento desligado | baixa | Nada foi enviado, assinado ou alterado |
| [`wallet_pin_invalid`](#wallet-pin-invalid) | PIN inválido | baixa | Nada foi enviado, assinado ou alterado |
| [`wallet_quote_expired`](#wallet-quote-expired) | A prévia expirou ou foi substituída | baixa | Nada foi enviado, assinado ou alterado |
| [`wallet_recipient_refused`](#wallet-recipient-refused) | O destinatário não pode receber isto em segurança | média | Nada foi enviado, assinado ou alterado |
| [`wallet_request_ambiguous`](#wallet-request-ambiguous) | Mais de uma conta ou rede serve | baixa | Nada foi enviado, assinado ou alterado |
| [`wallet_send_needs_vool_wallet`](#wallet-send-needs-vool-wallet) | Para enviar, é preciso um VOOL Wallet | baixa | Nada foi enviado, assinado ou alterado |
| [`wallet_setup_state_invalid`](#wallet-setup-state-invalid) | O passo de configuração não se aplica agora | baixa | Nada foi enviado, assinado ou alterado |
| [`wallet_signing_unavailable`](#wallet-signing-unavailable) | A carteira não pode assinar daqui | média | Nada foi enviado, assinado ou alterado |
| [`wallet_storage_class_refused`](#wallet-storage-class-refused) | Armazenamento de chaves só em memória | média | Nada foi enviado, assinado ou alterado |
| [`wallet_x402_cap_exceeded`](#wallet-x402-cap-exceeded) | Recurso pago acima do teto automático | média | Nada foi enviado, assinado ou alterado |
| [`wallet_x402_sdk_unavailable`](#wallet-x402-sdk-unavailable) | Extra x402 não instalado | média | Nada foi enviado, assinado ou alterado |
| [`x402_scheme_unavailable`](#x402-scheme-unavailable) | Esquema de pagamento não suportado | baixa | Nada foi enviado, assinado ou alterado |

## Disponibilidade

| Código | Título | Gravidade | O que pode já ter acontecido |
|---|---|---|---|
| [`provider_exhausted`](#provider-exhausted) | Limite do provedor atingido | média | Uma solicitação chegou a um serviço externo; nenhum pagamento foi feito |
| [`provider_unavailable`](#provider-unavailable) | Provedor inacessível | média | Nada foi enviado, assinado ou alterado |
| [`timeout`](#timeout) | Tempo esgotado | média | Resultado desconhecido — confira o estado antes de tentar de novo |
| [`tool_unavailable`](#tool-unavailable) | Ferramenta indisponível | baixa | Nada foi enviado, assinado ou alterado |
| [`wallet_broadcast_failed`](#wallet-broadcast-failed) | Não foi possível transmitir o pagamento | média | Resultado desconhecido — confira o estado antes de tentar de novo |
| [`wallet_device_auth_unavailable`](#wallet-device-auth-unavailable) | Destravamento pelo dispositivo indisponível | média | Nada foi enviado, assinado ou alterado |
| [`wallet_quote_unavailable`](#wallet-quote-unavailable) | Saldo ou taxa desconhecidos | média | Uma solicitação chegou a um serviço externo; nenhum pagamento foi feito |
| [`wallet_security_key_needed`](#wallet-security-key-needed) | Chave de segurança necessária | baixa | Nada foi enviado, assinado ou alterado |
| [`wallet_simulation_failed`](#wallet-simulation-failed) | O pagamento não passou na simulação | média | Uma solicitação chegou a um serviço externo; nenhum pagamento foi feito |

## Cancelamento

| Código | Título | Gravidade | O que pode já ter acontecido |
|---|---|---|---|
| [`cancelled`](#cancelled) | Cancelado | informativa | Resultado desconhecido — confira o estado antes de tentar de novo |

## Internos

| Código | Título | Gravidade | O que pode já ter acontecido |
|---|---|---|---|
| [`unknown`](#unknown) | Erro inesperado | média | Resultado desconhecido — confira o estado antes de tentar de novo |

## Referência por código

Todos os códigos, em ordem alfabética. O título da seção é a âncora fixa do código.

<a name="cancelled"></a>

### cancelled

**Cancelado**

- **O que aconteceu:** Isto foi cancelado, então não terminou.
- **O que pode já ter acontecido:** Resultado desconhecido — confira o estado antes de tentar de novo
- **O que fazer em seguida:** Nada é preciso; comece de novo se quiser que seja feito.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** informativa · **Categoria:** cancelamento

<a name="confinement-refusal"></a>

### confinement_refusal

**Mudança fora do escopo permitido**

- **O que aconteceu:** Uma mudança de arquivo fora do escopo permitido foi bloqueada. Nada fora do escopo foi tocado.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Se esta mudança é desejada, peça-a explicitamente para que fique dentro do escopo autorizado.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** alta · **Categoria:** segurança · **Relevante para a segurança:** sim

<a name="credential-failure"></a>

### credential_failure

**Não foi possível ler uma credencial armazenada**

- **O que aconteceu:** Uma credencial guardada não pôde ser lida. Digite-a de novo nos ajustes para continuar.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** A entrada do cofre não descriptografou; re guarde a credencial ou confira a chave do cofre.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** alta · **Categoria:** segurança · **Relevante para a segurança:** sim

<a name="evidence-corruption"></a>

### evidence_corruption

**Os registros da resposta falharam numa verificação de consistência**

- **O que aconteceu:** Os registros que sustentam esta resposta falharam numa verificação de consistência, trate a resposta como não verificada.
- **O que pode já ter acontecido:** Houve um registro ou uma alteração local
- **O que fazer em seguida:** Investigue o livro de execução deste turno contra o fluxo de eventos do runtime.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** alta · **Categoria:** integridade · **Relevante para a segurança:** sim

<a name="evm-pocket-custody-unavailable"></a>

### evm_pocket_custody_unavailable

**Bolso legado só para Solana**

- **O que aconteceu:** Um bolso legado, a carteira baseada numa frase de recuperação, guarda só uma chave Solana, então não pode ser criado nem restaurado numa rede EVM. Nada foi criado. Para uma rede EVM, crie uma Carteira VOOL nessa rede em Configurações, Cripto, adicione a conta como somente-leitura ou conecte sua própria carteira EVM.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** O bolso legado sela uma única semente Solana da sua frase de recuperação. Numa rede EVM, use uma carteira Crypto Pilot ("Create wallet" nessa rede, em Configurações, Cripto, Redes e carteiras), uma conta somente-leitura ou uma carteira EVM externa (EIP-1193) que guarda a própria chave.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** baixa · **Categoria:** política

<a name="integrity-verification-failure"></a>

### integrity_verification_failure

**Um registro falhou na verificação à prova de adulteração**

- **O que aconteceu:** Um registro falhou na verificação à prova de adulteração. Nada foi aceito em silêncio.
- **O que pode já ter acontecido:** Houve um registro ou uma alteração local
- **O que fazer em seguida:** Trate a cadeia de recibos afetada como comprometida; investigue antes de confiar nas entradas.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** crítica · **Categoria:** integridade · **Relevante para a segurança:** sim

<a name="permission-denied"></a>

### permission_denied

**Permissão negada**

- **O que aconteceu:** Essa ação não foi permitida, então nada foi mudado.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Abra as Configurações, escolha Privacidade e permissões e defina «O que o VOOL pode fazer sozinho» para mudar o que rege esta ação.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** média · **Categoria:** política · **Relevante para a segurança:** sim

<a name="provider-exhausted"></a>

### provider_exhausted

**Limite do provedor atingido**

- **O que aconteceu:** O limite de uso do provedor de modelos foi atingido. Você pode tentar de novo mais tarde.
- **O que pode já ter acontecido:** Uma solicitação chegou a um serviço externo; nenhum pagamento foi feito
- **O que fazer em seguida:** Confira cota, limites de taxa e cobrança do provedor configurado.
- **Tentar de novo:** Tente de novo mais tarde: o que impediu isto se resolve com o tempo.
- **Gravidade:** média · **Categoria:** disponibilidade

<a name="provider-unavailable"></a>

### provider_unavailable

**Provedor inacessível**

- **O que aconteceu:** O provedor de modelos não pôde ser alcançado. Você pode tentar de novo em instantes.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Confira o estado do provedor, a alcançabilidade da rede e os pontos configurados.
- **Tentar de novo:** Tente de novo mais tarde: o que impediu isto se resolve com o tempo.
- **Gravidade:** média · **Categoria:** disponibilidade

<a name="timeout"></a>

### timeout

**Tempo esgotado**

- **O que aconteceu:** Isto demorou demais e foi parado. Tentar de novo pode dar certo.
- **O que pode já ter acontecido:** Resultado desconhecido — confira o estado antes de tentar de novo
- **O que fazer em seguida:** Se os tempos esgotados se repetem, confira a latência da rede e a resposta do provedor.
- **Tentar de novo:** Tentar de novo agora pode dar certo.
- **Gravidade:** média · **Categoria:** disponibilidade

<a name="tool-unavailable"></a>

### tool_unavailable

**Ferramenta indisponível**

- **O que aconteceu:** Essa ferramenta não está disponível agora, então esta parte não foi feita. É preciso ativá-la antes de tentar de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Abra as Configurações, escolha Privacidade e permissões e defina «O que o VOOL pode fazer sozinho», ou escolha outra abordagem.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** disponibilidade

<a name="unknown"></a>

### unknown

**Erro inesperado**

- **O que aconteceu:** Algo deu errado nesta máquina ao tratar disso. Você pode tentar de novo.
- **O que pode já ter acontecido:** Resultado desconhecido — confira o estado antes de tentar de novo
- **O que fazer em seguida:** Veja a cadeia de causas editada da falha nos diagnósticos; uma causa desconhecida nunca é repetida às cegas.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** média · **Categoria:** interna

<a name="unsupported-claim"></a>

### unsupported_claim

**Afirmações sem apoio retidas**

- **O que aconteceu:** Uma ou mais afirmações não tinham apoio das fontes recuperadas e foram retidas.
- **O que pode já ter acontecido:** Houve um registro ou uma alteração local
- **O que fazer em seguida:** Nada é preciso — é a porta de fundamentação funcionando. Se frequente, confira a qualidade da busca.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** média · **Categoria:** integridade

<a name="wallet-acknowledgement-required"></a>

### wallet_acknowledgement_required

**Confirmação necessária**

- **O que aconteceu:** Este passo precisa primeiro da sua confirmação, então nada foi assinado nem enviado. Leia o que a carteira diz que esta ação faz, confirme exatamente e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Quando uma aprovação daria direitos de gasto permanentes, mudaria a propriedade ou rodaria uma atualização, ou a carteira encontra uma divergência ou não consegue ler a solicitação, ela pede que você digite primeiro o texto de confirmação exato; nada genérico vale no lugar dele.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** política

<a name="wallet-amount-invalid"></a>

### wallet_amount_invalid

**Valor não exato para esta moeda**

- **O que aconteceu:** Esse valor não é um valor exato que esta moeda comporta, então nada foi proposto. Confira o valor e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Envie um valor decimal simples, não mais preciso do que a moeda permite e dentro do limite por transferência do piloto.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** política

<a name="wallet-approval-rejected"></a>

### wallet_approval_rejected

**Aprovação não aceita**

- **O que aconteceu:** A aprovação deste pagamento não foi aceita, então nada foi assinado ou enviado.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Aprove os parâmetros exatos com o PIN ou a biometria do dono; aprovações não transferem entre parâmetros.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** média · **Categoria:** política · **Relevante para a segurança:** sim

<a name="wallet-backup-unavailable"></a>

### wallet_backup_unavailable

**Backup já mostrado uma vez**

- **O que aconteceu:** A chave de backup desta carteira já foi mostrada uma vez, então não pode ser mostrada de novo. Se você não a anotou, cancele a configuração e não envie fundos para este endereço.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Um backup do Piloto Cripto é revelado exatamente uma vez na configuração; não há segunda revelação nem exportação.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** alta · **Categoria:** segurança · **Relevante para a segurança:** sim

<a name="wallet-broadcast-failed"></a>

### wallet_broadcast_failed

**Não foi possível transmitir o pagamento**

- **O que aconteceu:** O pagamento não pôde ser transmitido. Confira o estado dele antes de tentar de novo.
- **O que pode já ter acontecido:** Resultado desconhecido — confira o estado antes de tentar de novo
- **O que fazer em seguida:** Confira o endpoint RPC da rede e o recibo da proposta; o gasto não foi registrado como enviado.
- **Tentar de novo:** Tente de novo mais tarde: o que impediu isto se resolve com o tempo.
- **Gravidade:** média · **Categoria:** disponibilidade

<a name="wallet-caller-refused"></a>

### wallet_caller_refused

**Não iniciado a partir da própria janela do VOOL**

- **O que aconteceu:** Esta ação de carteira só pode começar da própria janela do VOOL neste computador, então nada foi mudado, assinado ou enviado. Abra o VOOL e tente lá.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Dirija portas de carteira confiáveis pela página servida ou pela janela nativa; uma página de outra origem, um frame ou um plugin é recusado.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** alta · **Categoria:** segurança · **Relevante para a segurança:** sim

<a name="wallet-card-data-refused"></a>

### wallet_card_data_refused

**Dados de cartão recusados**

- **O que aconteceu:** Números de cartão e códigos de segurança nunca são guardados aqui; só um token do provedor é aceito. Nada foi salvo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Tokenize o cartão com o provedor e registre a referência do token.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** alta · **Categoria:** segurança · **Relevante para a segurança:** sim

<a name="wallet-chain-identity-mismatch"></a>

### wallet_chain_identity_mismatch

**Identidade do ponto da cadeia não provada**

- **O que aconteceu:** O ponto da cadeia não provou a identidade da rede que diz ser, então nada foi assinado ou enviado.
- **O que pode já ter acontecido:** Uma solicitação chegou a um serviço externo; nenhum pagamento foi feito
- **O que fazer em seguida:** Confira o ponto RPC da rede; um chain id ou genesis mentiroso é recusado antes de qualquer pagamento.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** alta · **Categoria:** segurança · **Relevante para a segurança:** sim

<a name="wallet-confirmation-required"></a>

### wallet_confirmation_required

**Confirmação digitada necessária**

- **O que aconteceu:** Aquele passo exige primeiro a confirmação digitada, então nada foi mudado. Confirme e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Leia o aviso e digite a frase de confirmação exata para prosseguir.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** política

<a name="wallet-credential-mismatch"></a>

### wallet_credential_mismatch

**As entradas não conferiram**

- **O que aconteceu:** As duas entradas não conferiram, então nada foi criado. Digite o mesmo PIN ou senha duas vezes e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Uma credencial do Piloto Cripto é confirmada antes de qualquer material de chave existir.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** política

<a name="wallet-dependency-unavailable"></a>

### wallet_dependency_unavailable

**Bibliotecas EVM não instaladas**

- **O que aconteceu:** Esta versão não consegue assinar EVM nem codificar ABI: as bibliotecas Ethereum não estão instaladas nesta máquina. Nada foi assinado ou enviado. Solana, custódia e tetos de gasto não são afetados; com as bibliotecas instaladas, tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Instale os extras EVM (eth-abi, eth-utils, eth-account) para ativar as pistas EVM; toda outra função da carteira funciona sem eles.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** média · **Categoria:** política

<a name="wallet-device-auth-denied"></a>

### wallet_device_auth_denied

**Destravamento pelo dispositivo recusado**

- **O que aconteceu:** Touch ID ou a senha do seu Mac foi recusado ou não abriu esta carteira, então nada foi criado, alterado, destravado, revelado, assinado ou enviado. Tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Tente de novo e confirme o pedido de Touch ID ou da senha do Mac mostrado pelo VOOL. Uma carteira que aprova com Touch ID não tem PIN nem senha próprios para usar no lugar. Se o pedido continuar falhando, restaure a carteira a partir da frase de recuperação (um Crypto Pilot, a partir do backup mostrado na configuração) e dê a ela um PIN ou uma senha; uma exportação da chave privada continua precisando de Touch ID ou da senha do Mac.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** política

<a name="wallet-device-auth-unavailable"></a>

### wallet_device_auth_unavailable

**Destravamento pelo dispositivo indisponível**

- **O que aconteceu:** Touch ID ou a senha do seu Mac não está disponível aqui para esta carteira, então nada foi criado, alterado, destravado, revelado, assinado ou enviado. Tente de novo quando estiver disponível.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Uma carteira nova pode aprovar com o próprio PIN ou senha no lugar. Uma carteira que aprova com Touch ID não tem PIN nem senha próprios: se o Touch ID e a senha do Mac continuarem indisponíveis, restaure-a a partir da frase de recuperação (um Crypto Pilot, a partir do backup mostrado na configuração) e dê a ela um PIN ou uma senha. Uma exportação da chave privada só abre com Touch ID ou a senha do Mac, então restaure a carteira num Mac onde eles funcionem e exporte lá.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** média · **Categoria:** disponibilidade

<a name="wallet-disabled"></a>

### wallet_disabled

**A carteira está desligada**

- **O que aconteceu:** A carteira está desligada, então nenhum pagamento foi proposto ou enviado. Ligue "Ativar Cripto" em Configurações, Cripto e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Ligue "Ativar Cripto" em Configurações, Cripto só se você quiser pagamentos nesta instalação. Quando a variável VOOL_WALLET_ENABLED está definida, ela liga ou desliga a carteira, seja qual for a posição do interruptor.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** política

<a name="wallet-duplicate-payment"></a>

### wallet_duplicate_payment

**Pagamento já proposto ou enviado**

- **O que aconteceu:** Este pagamento já tinha sido proposto ou enviado, então não foi enviado de novo.
- **O que pode já ter acontecido:** Houve um registro ou uma alteração local
- **O que fazer em seguida:** Confira o recibo da proposta existente; use uma chave de idempotência nova para um pagamento realmente novo.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** média · **Categoria:** integridade · **Relevante para a segurança:** sim

<a name="wallet-environment-inactive"></a>

### wallet_environment_inactive

**Ambiente de rede errado**

- **O que aconteceu:** Aquela rede pertence ao outro ambiente de rede (Mainnet ou Redes de teste), então nada foi criado, proposto ou assinado. Troque em Ajustes, Cripto, Opções de desenvolvedor, e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Escolha o ambiente de rede deliberadamente nos ajustes de Cripto; contas, saldos e recibos nunca viajam entre ambientes.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** média · **Categoria:** segurança · **Relevante para a segurança:** sim

<a name="wallet-export-pin-not-accepted"></a>

### wallet_export_pin_not_accepted

**A exportação só é destravada pela autenticação do dispositivo**

- **O que aconteceu:** A exportação nunca é destravada com PIN, senha ou frase-senha, só com a autenticação do dispositivo (Touch ID ou a senha do seu Mac), então nada foi revelado. Tente de novo sem o PIN ou a senha.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** A exportação só é destravada pela autenticação do dispositivo, de propósito; um PIN, uma senha ou uma frase-senha na solicitação é recusado antes mesmo de ser verificado.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** alta · **Categoria:** segurança · **Relevante para a segurança:** sim

<a name="wallet-export-refused"></a>

### wallet_export_refused

**Forma antiga de exportar aposentada**

- **O que aconteceu:** Essa forma de exportar uma chave privada foi aposentada, então nada foi revelado. Uma carteira que permite exportação tem o botão "Export private key" em Configurações, Cripto, que só abre com Touch ID ou a senha do seu Mac.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Use "Export private key" em Configurações, Cripto, Outras contas: só o bolso legado é exportado, e só com Touch ID ou a senha do Mac. Uma carteira Crypto Pilot nunca é exportada; ela é recuperada a partir do backup mostrado na configuração.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** alta · **Categoria:** segurança · **Relevante para a segurança:** sim

<a name="wallet-export-unavailable"></a>

### wallet_export_unavailable

**Exportação indisponível para esta carteira**

- **O que aconteceu:** Esta carteira não pode ser exportada assim: ela é somente-leitura ou não guarda nenhuma chave neste computador, seu único backup foi mostrado na configuração e ela nunca é exportada, o VOOL não exporta para o app de carteira que você escolheu, ou é um bolso legado de uma versão anterior que precisa ser restaurado a partir da frase de recuperação antes de exportar para o MetaMask. Nada foi revelado.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Só um bolso legado selado neste computador é exportado, para Phantom ou MetaMask, e só com Touch ID ou a senha do Mac. Uma carteira Crypto Pilot é recuperada a partir do backup mostrado na configuração. Um bolso legado de uma versão anterior é exportado para o MetaMask depois de restaurado a partir da frase de recuperação.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** alta · **Categoria:** segurança · **Relevante para a segurança:** sim

<a name="wallet-insufficient-funds"></a>

### wallet_insufficient_funds

**O saldo não cobre o valor e a taxa**

- **O que aconteceu:** O saldo não cobre o valor mais o máximo que a taxa de rede pode ser, então nada foi preparado. Baixe o valor ou adicione fundos e tente de novo.
- **O que pode já ter acontecido:** Uma solicitação chegou a um serviço externo; nenhum pagamento foi feito
- **O que fazer em seguida:** O gastável exclui outras retenções em curso; um resto na Solana deve terminar sem zeros e cobrir ao menos o aluguel.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** política

<a name="wallet-legacy-surface-retired"></a>

### wallet_legacy_surface_retired

**Caminho de dinheiro legado aposentado**

- **O que aconteceu:** Aquele caminho de dinheiro foi aposentado: todo pagamento passa pelo fluxo de proposta e aprovação da carteira. Nada foi assinado ou enviado.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Use as ferramentas da carteira (wallet.propose) ou a API da carteira; o assinante, pagador e exportador legados não servem mais.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** alta · **Categoria:** segurança · **Relevante para a segurança:** sim

<a name="wallet-limit-exceeded"></a>

### wallet_limit_exceeded

**Limite de gasto excedido**

- **O que aconteceu:** Aquele pagamento está acima de um limite de gasto, então não foi enviado.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Revise os limites por transação, diários e por destino antes de tentar de novo.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** média · **Categoria:** política · **Relevante para a segurança:** sim

<a name="wallet-network-disabled"></a>

### wallet_network_disabled

**Rede não declarada para esta carteira**

- **O que aconteceu:** Aquela rede não é uma que esta carteira declara para esta ação, então nada foi criado ou enviado.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Use uma das linhas de rede declaradas; cadeias não declaradas, apelidos e nomes parecidos são recusados pelo registro.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** alta · **Categoria:** segurança · **Relevante para a segurança:** sim

<a name="wallet-not-found"></a>

### wallet_not_found

**Carteira, proposta ou destino não reconhecido**

- **O que aconteceu:** Aquela carteira, proposta ou destino não foi reconhecido, então nada foi feito. Confira e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Confira o id da carteira, o id da proposta ou o endereço de destino e tente de novo.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** política

<a name="wallet-outbound-refused"></a>

### wallet_outbound_refused

**Pedido fora dos pontos aprovados**

- **O que aconteceu:** Aquele pedido não tinha permissão para sair dos pontos aprovados da carteira, então nenhuma conexão foi feita.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Confira as origens RPC declaradas e a política de recursos de pagamento; hosts privados, de metadados e não aprovados são recusados.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** alta · **Categoria:** segurança · **Relevante para a segurança:** sim

<a name="wallet-password-invalid"></a>

### wallet_password_invalid

**Senha da carteira inválida**

- **O que aconteceu:** A senha não cumpriu a política ou não destravou a carteira, então nada foi assinado nem mudado. Confira a senha e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** As senhas da carteira têm de 10 a 128 caracteres e não são só dígitos. Uma senha errada deixa a chave selada e conta para o bloqueio da carteira depois de tentativas erradas demais.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** política

<a name="wallet-paykit-unavailable"></a>

### wallet_paykit_unavailable

**Extra pay da Solana não instalado**

- **O que aconteceu:** Esta versão não consegue pagar ofertas x402 ou MPP na Solana: o extra opcional pay (Solana pay-kit, Python 3.11 ou mais recente) não está instalado nesta máquina. Nada foi assinado ou enviado. Toda outra função da carteira funciona sem ele; com ele instalado, tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Instale o extra opcional pay do VOOL no Python 3.11 ou mais recente para ativar os pagamentos pay-kit; a carteira mantém em qualquer caso o próprio assinante, os tetos de gasto e a aprovação.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** média · **Categoria:** política

<a name="wallet-payment-channel-off"></a>

### wallet_payment_channel_off

**Canal de pagamento desligado**

- **O que aconteceu:** Esse pagamento passa por um canal de pagamento desligado, então nada foi assinado ou enviado. Ligue o canal em Ajustes, Cripto, Canais de pagamento, e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Ligue um canal de pagamento nos ajustes de Cripto só se quiser que pagamentos passem por ele; o teto e a sua aprovação valem em todos os canais.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** política

<a name="wallet-pin-invalid"></a>

### wallet_pin_invalid

**PIN inválido**

- **O que aconteceu:** O PIN não cumpriu a política ou não destravou a carteira, então nada foi assinado. Confira o PIN e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Use um PIN de 6 a 12 dígitos; um PIN errado deixa a chave selada intacta.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** política

<a name="wallet-quote-expired"></a>

### wallet_quote_expired

**A prévia expirou ou foi substituída**

- **O que aconteceu:** Esta prévia de transferência expirou ou foi substituída, então não pode ser aprovada e nada foi enviado. Atualize a prévia e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Uma cotação vive pouco e é substituída por uma atualização ou troca de ambiente; a aprovação sempre liga a atual.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** política

<a name="wallet-quote-mismatch"></a>

### wallet_quote_mismatch

**A aprovação não corresponde mais à prévia**

- **O que aconteceu:** A aprovação já não corresponde à transferência pré-visualizada, então nada foi assinado ou enviado. Recomece de uma prévia nova.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Uma aprovação liga o resumo de uma cotação; conta, cadeia, destinatário, valor, ambiente ou taxa mudados exigem aprovação nova.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** alta · **Categoria:** segurança · **Relevante para a segurança:** sim

<a name="wallet-quote-unavailable"></a>

### wallet_quote_unavailable

**Saldo ou taxa desconhecidos**

- **O que aconteceu:** A rede não deu uma resposta completa para o saldo ou a taxa desta transferência, então nenhuma aprovação foi preparada e nada foi enviado. Tente de novo em instantes.
- **O que pode já ter acontecido:** Uma solicitação chegou a um serviço externo; nenhum pagamento foi feito
- **O que fazer em seguida:** Dados desconhecidos da cadeia nunca são tratados como zero; confira o ponto da linha e a prova de identidade dele.
- **Tentar de novo:** Tente de novo mais tarde: o que impediu isto se resolve com o tempo.
- **Gravidade:** média · **Categoria:** disponibilidade

<a name="wallet-recipient-refused"></a>

### wallet_recipient_refused

**O destinatário não pode receber isto em segurança**

- **O que aconteceu:** Aquele destinatário não pode receber esta transferência em segurança nesta rede, então nada foi preparado. Confira o endereço e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Recusado: uma conta nova na Solana abaixo do aluguel mínimo, uma conta executável ou fora da curva, uma soma de verificação EIP-55 inválida, o endereço zero, pré-compilados e contratos do sistema.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** média · **Categoria:** política

<a name="wallet-recovery-refused"></a>

### wallet_recovery_refused

**A prova de recuperação não serve para esta carteira**

- **O que aconteceu:** Aquela prova de recuperação não serve para esta carteira, então nada foi mudado: a chave salva precisa ser o backup que o VOOL mostrou para este endereço exato, no formato em que foi mostrada. Confira e tente de novo, ou use as outras opções.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** A recuperação resela uma carteira existente somente de um backup que decodifica para a própria chave da carteira (as duas direções).
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** alta · **Categoria:** segurança · **Relevante para a segurança:** sim

<a name="wallet-request-ambiguous"></a>

### wallet_request_ambiguous

**Mais de uma conta ou rede serve**

- **O que aconteceu:** Mais de uma conta ou rede serve para esta transferência, então nada foi proposto. Diga qual conta ou rede você quer e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Uma transferência nomeia uma conta numa rede; os candidatos estão listados no contexto da falha.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** política

<a name="wallet-security-key-needed"></a>

### wallet_security_key_needed

**Chave de segurança necessária**

- **O que aconteceu:** Esta carteira também precisa da chave de segurança dela, e a chave não respondeu: ela não está conectada, é outra chave ou não foi tocada a tempo. Nada foi desbloqueado, alterado, assinado ou enviado. Conecte a chave de segurança desta carteira, toque nela quando piscar e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Conecte a chave de segurança adicionada a esta carteira e toque nela quando piscar. Se você perdeu a chave, recupere a carteira com a chave de backup dela (anotada no papel, no Keychain do macOS ou num pendrive): isso define um novo PIN e remove a chave perdida da carteira.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** disponibilidade

<a name="wallet-send-needs-vool-wallet"></a>

### wallet_send_needs_vool_wallet

**Para enviar, é preciso um VOOL Wallet**

- **O que aconteceu:** Esta conta não pode enviar pelo VOOL nesta rede: aqui só envia um VOOL Wallet criado neste aparelho. Uma conta conectada ou somente-leitura ainda pode receber e mostrar o saldo. Nada foi assinado nem enviado. Para enviar, escolha um VOOL Wallet neste aparelho e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Envie de um VOOL Wallet neste aparelho, ou pelo próprio app da carteira conectada; nesta rede o VOOL não assina nada para uma conta conectada ou somente-leitura.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** política

<a name="wallet-setup-state-invalid"></a>

### wallet_setup_state_invalid

**O passo de configuração não se aplica agora**

- **O que aconteceu:** Aquele passo de configuração não se aplica a esta carteira agora, então nada mudou. Confira o estado de configuração da carteira e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** A configuração avança: gerando, aguardando backup, backup revelado, pronto; cancelar e retomar nunca revelam um backup duas vezes.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** política

<a name="wallet-signature-invalid"></a>

### wallet_signature_invalid

**A assinatura não correspondeu à chave da carteira**

- **O que aconteceu:** A assinatura devolvida para este pagamento não correspondeu à chave da carteira, então não foi transmitida.
- **O que pode já ter acontecido:** Uma solicitação chegou a um serviço externo; nenhum pagamento foi feito
- **O que fazer em seguida:** Confira o assinante conectado; uma assinatura divergente é recusada antes da transmissão.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** alta · **Categoria:** segurança · **Relevante para a segurança:** sim

<a name="wallet-signing-unavailable"></a>

### wallet_signing_unavailable

**A carteira não pode assinar daqui**

- **O que aconteceu:** Esta carteira não pode assinar daqui: é somente-leitura ou o assinante externo não está conectado. Nada foi enviado; conecte um assinante e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Conecte a carteira externa, ou crie uma Carteira VOOL nessa rede em Configurações, Cripto.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** média · **Categoria:** política

<a name="wallet-simulation-failed"></a>

### wallet_simulation_failed

**O pagamento não passou na simulação**

- **O que aconteceu:** O pagamento não passou na simulação, então não foi enviado. Confira o saldo e o destino e tente de novo.
- **O que pode já ter acontecido:** Uma solicitação chegou a um serviço externo; nenhum pagamento foi feito
- **O que fazer em seguida:** Confira o saldo, o destino e o endpoint RPC da rede, e proponha de novo.
- **Tentar de novo:** Tente de novo mais tarde: o que impediu isto se resolve com o tempo.
- **Gravidade:** média · **Categoria:** disponibilidade

<a name="wallet-storage-class-refused"></a>

### wallet_storage_class_refused

**Armazenamento de chaves só em memória**

- **O que aconteceu:** Esta instalação guarda a chave do aparelho só em memória, então uma carteira criada aqui não poderia ser aberta depois de reiniciar. Nada foi criado. Mude o armazenamento de chaves para um modo durável e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Carteiras do Piloto Cripto são recusadas enquanto VOOL_KEY_STORAGE_MODE for ephemeral; use armazenamento file ou keyring.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** média · **Categoria:** política

<a name="wallet-unlock-throttled"></a>

### wallet_unlock_throttled

**Carteira trancada após destravamentos malsucedidos**

- **O que aconteceu:** Houve tentativas demais de PIN ou senha errados, então esta carteira está trancada por um tempo e nada foi destravado. Espere e tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Destravamentos falidos são contados de forma durável por carteira e para todo o app, entre reinícios; a tranca levanta sozinha.
- **Tentar de novo:** Tente de novo mais tarde: o que impediu isto se resolve com o tempo.
- **Gravidade:** alta · **Categoria:** segurança · **Relevante para a segurança:** sim

<a name="wallet-x402-cap-exceeded"></a>

### wallet_x402_cap_exceeded

**Recurso pago acima do teto automático**

- **O que aconteceu:** Um recurso pago pediu mais do que o teto de pagamento automático, então não foi pago.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Aumente VOOL_WALLET_X402_CAP_MINOR deliberadamente, ou pague o recurso com uma proposta explícita.
- **Tentar de novo:** Não envie a mesma solicitação de novo sem alterações.
- **Gravidade:** média · **Categoria:** política · **Relevante para a segurança:** sim

<a name="wallet-x402-sdk-unavailable"></a>

### wallet_x402_sdk_unavailable

**Extra x402 não instalado**

- **O que aconteceu:** Esta versão não consegue pagar ofertas x402 na Base pelo x402 Foundation SDK: o extra opcional x402 não está instalado nesta máquina. Nada foi assinado ou enviado. Toda outra função da carteira funciona sem ele; com ele instalado, tente de novo.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Instale o extra opcional x402 do VOOL para pagar ofertas x402 na Base a partir de uma carteira Crypto Pilot; a carteira mantém em qualquer caso o próprio assinante, os tetos de gasto e a aprovação.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** média · **Categoria:** política

<a name="x402-scheme-unavailable"></a>

### x402_scheme_unavailable

**Esquema de pagamento não suportado**

- **O que aconteceu:** Esta oferta de pagamento exige um esquema que esta carteira não pode provar ponta a ponta, então nada foi assinado. Tente uma oferta cujo esquema, token e facilitador estejam verificados para esta rede.
- **O que pode já ter acontecido:** Nada foi enviado, assinado ou alterado
- **O que fazer em seguida:** Use uma oferta cujo esquema, token e facilitador estejam verificados para a rede; suporte parcial de assinatura nunca é improvisado.
- **Tentar de novo:** Tente de novo depois de fazer a mudança que esta entrada indica.
- **Gravidade:** baixa · **Categoria:** política
