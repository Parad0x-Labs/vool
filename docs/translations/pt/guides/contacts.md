---
description: Contatos — pessoas e destinos salvos, com uma revisão de alterações que protege cada edição.
translation: machine
source_sha256: 8be5d771229a4d6c4b439af014f0407649ed694fd26f9fd880009d25f5b8a347
---

# Contatos

A lista de contatos é uma agenda de endereços local e estruturada (pessoas, os destinos delas e os aliases
de pagamento) que o VOOL pode usar em vez de pedir que você digite um endereço de novo. Como uma agenda de
endereços alimenta pagamentos e mensagens, as edições nela são **protegidas**: revisadas, confirmadas e
registradas.

## Edições protegidas

Alterar um contato salvo é um fluxo de duas etapas:

1. **Revisar** — a alteração é preparada e mostrada. Se a entrada mudou desde que você a abriu
   (outra janela, outro dispositivo), a alteração é recusada com *“changed after this was
   reviewed; review it again. Nothing changed.”*
2. **Confirmar** — a alteração só é gravada com o seu PIN ou a sua senha na tela de revisão.

Toda recusa diz claramente que **nada foi salvo nem alterado** — porque nada foi. Se
o seu PIN foi aceito mas o salvamento em si falhou, a mensagem diz exatamente isso: o PIN foi aceito;
salve de novo antes que a confirmação expire.

## Proteções

* Uma alteração por contato de cada vez; um número limitado de alterações pendentes.
* Excluir um contato não reescreve o histórico em silêncio: referências a um contato excluído são
  informadas como *“That contact was deleted”* — nunca resolvidas de novo em silêncio.
* Importações nunca são aplicadas pela metade: se a origem não pôde ser lida, nada foi importado.

## Por que este design

Um endereço de pagamento com um erro de digitação é dinheiro irrecuperável. A etapa de revisão existe para que
nenhum turno do modelo, nenhuma edição em massa e nenhuma janela desatualizada possam mudar a quem “Alice”
corresponde sem que uma pessoa confirme exatamente a entrada resultante.
