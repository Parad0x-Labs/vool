---
description: Como ler um erro do VOOL, se algo já aconteceu e onde cada código é explicado.
translation: machine
source_sha256: ccb01d4c778254a055fd67f46241c670aac90cfc83f168b923a19cef1f949de2
---

# Erros e códigos de erro

Quando algo falha, o VOOL diz em palavras simples o que aconteceu, o que pode já ter acontecido
e o que fazer em seguida. Cada falha também tem um código estável. Você o encontra nos detalhes técnicos
abaixo de um erro, na linha de comando (`wallet_export_refused: …`) e nas respostas da API. Esta
página explica como ler um erro. O [Livro de erros](../ERROR_BOOK.md) explica cada código.

## Como ler um erro

Confira três coisas antes de tentar de novo:

1. **O que aconteceu.** A mensagem. Ela nunca contém caminhos internos nem segredos.
2. **O que pode já ter acontecido.** A maioria das recusas para antes que qualquer coisa seja enviada ou
   alterada, e a mensagem diz isso. Algumas falhas não têm como saber se uma ação terminou, como um tempo
   esgotado depois que um pedido saiu. Nesse caso, a mensagem diz que o resultado é desconhecido e pede que
   você confira antes de tentar de novo. Um pagamento nunca é enviado duas vezes por acidente: um segundo envio
   do mesmo pagamento é recusado com
   [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment).
3. **O que fazer em seguida.** Um passo concreto.

O código carrega o significado. Um status HTTP como 403 ou 429 só diz como um pedido se saiu
no caminho, e duas recusas diferentes podem ter o mesmo status. Leia o código, não o status.

## Os códigos e o Livro de erros

Um código nunca é renomeado nem reutilizado com outro significado. O
[Livro de erros](../ERROR_BOOK.md) é gerado a partir do próprio catálogo de erros do app, então os códigos, as
mensagens e os próximos passos dele são exatamente o que o app mostra. Cada código tem a própria seção:
pesquise na página o código como ele foi informado a você, ou crie um link para a seção com o
endereço do livro, `#` e o código com os sublinhados trocados por hifens, como
[`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed).

Os códigos que você tem mais chance de ver:

| Código | Título | O que pode já ter acontecido |
| --- | --- | --- |
| [`permission_denied`](../ERROR_BOOK.md#permission-denied) | Permissão negada | Nada foi alterado. |
| [`provider_unavailable`](../ERROR_BOOK.md#provider-unavailable) | Provedor inacessível | Nada foi enviado. |
| [`provider_exhausted`](../ERROR_BOOK.md#provider-exhausted) | Limite do provedor atingido | O pedido chegou ao provedor. |
| [`timeout`](../ERROR_BOOK.md#timeout) | Tempo esgotado | Desconhecido: confira antes de tentar de novo. |
| [`cancelled`](../ERROR_BOOK.md#cancelled) | Cancelado | Desconhecido: parte dele pode já ter rodado. |
| [`credential_failure`](../ERROR_BOOK.md#credential-failure) | Não foi possível ler uma credencial armazenada | Nada foi enviado nem alterado. |
| [`unknown`](../ERROR_BOOK.md#unknown) | Erro inesperado | Desconhecido: confira antes de tentar de novo. |
| [`wallet_quote_mismatch`](../ERROR_BOOK.md#wallet-quote-mismatch) | A aprovação não corresponde mais à prévia | Nada foi assinado nem enviado. |
| [`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed) | Não foi possível transmitir o pagamento | Desconhecido: confira o status do pagamento antes de tentar de novo. |
| [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment) | Pagamento já proposto ou enviado | Nada novo foi enviado; o pagamento anterior está registrado. |

## Erros e outros resultados

O VOOL mantém estes casos separados, e o texto dele também:

* **Um erro.** Algo falhou. O código e a mensagem dizem o quê, e o que fazer.
* **Esperando por você.** Um pedido de permissão ou uma aprovação de pagamento está aberto. Nada rodou,
  e nada vai rodar até você responder. Veja [Permissões](../concepts/permissions.md).
* **Parado por um limite de gastos ou por uma proteção de preço.** A recusa acontece antes de o provedor ser
  contatado, e a mensagem nunca dá a entender que o provedor foi consultado. Veja
  [Limites de gastos e proteções de preço](../guides/spending-limits.md).
* **Cancelado.** Você, ou o VOOL, parou a ação antes de ela terminar. Parte dela pode já ter rodado,
  então confira o que mudou antes de começar de novo.
* **Não é possível aqui.** Esta versão ou esta máquina não consegue fazer isso, e a mensagem diz o que
  consegue: uma oferta de pagamento cujo esquema o VOOL não consegue verificar de ponta a ponta, ou um extra
  opcional que não está instalado.
* **Resultado desconhecido.** A ação pode ou não ter terminado, como um pagamento cuja
  transmissão nunca foi confirmada. Confira primeiro o status dela. O VOOL nunca paga de novo às cegas.

## Erros com códigos próprios

Algumas partes do VOOL explicam as próprias falhas com códigos curtos próprios, na tela onde a
falha acontece, e não estão no Livro de erros: anexos (por exemplo, um arquivo grande
demais ou uma credencial encontrada em um arquivo colado), atualizações do app, pastas de projeto, seleção de
modelo, limites de gastos, contatos, plugins e habilidades, entrada de voz, dados armazenados e relatórios de
bugs. As mensagens deles dizem o que aconteceu e o que fazer da mesma forma. Veja
[Conversas e anexos](../concepts/chats-and-attachments.md),
[Atualizações e migração](updates-migration.md) e
[Relato de bugs e privacidade](../trust/bug-reporting.md).

## Um código que você não reconhece

Um código que não está no Livro de erros não veio desta versão do VOOL. Confira qual
versão o mostrou e depois pesquise no [Livro de erros](../ERROR_BOOK.md) daquela versão. Se ele
veio desta versão, relate-o com o código exatamente como escrito, e com a referência
(`fault-` e doze caracteres) quando a resposta incluiu uma. Uma falha que o VOOL não consegue
classificar aparece como [`unknown`](../ERROR_BOOK.md#unknown), nunca como uma causa adivinhada.
Veja [Relato de bugs e privacidade](../trust/bug-reporting.md).

## Solucionar sintomas específicos

Veja [Solução de problemas](../help/troubleshooting.md) para problemas de instalação, de modelos e de
plataforma, e [Backup e recuperação](../help/backup-and-recovery.md) para recuperar as suas conversas, a sua
memória e a sua carteira.
