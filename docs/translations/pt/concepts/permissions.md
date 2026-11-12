---
description: Os cinco modos, o que cada um deixa o VOOL fazer sem perguntar e o que cada botão de aprovação cobre.
translation: machine
source_sha256: 1f33c8d63bcdeddbd1ac44d3b2d948b354137954ee6accb6ee18eeec8bdda1c6
---

# Permissões

O VOOL decide o que pode rodar antes que qualquer coisa rode. O modelo pode pedir uma ação; o modo que você
escolheu e as suas respostas decidem se ela acontece. Ler é tratado de forma diferente de
alterar, e segredos, pagamentos e configurações de segurança são protegidos em todos os modos.

## Os modos

Escolha o modo no menu ao lado da caixa de mensagem. Uma conversa nova começa em **Manual**.

| Modo | Roda sem perguntar | Pergunta antes | Nunca |
| --- | --- | --- | --- |
| Manual | Ler e listar arquivos, comandos seguros e consultas públicas à web somente leitura | Toda alteração: edições de arquivos, comandos, ações de rede, configurações, Git, implantação e mensagens | Ler segredos armazenados |
| Revisar edições | O mesmo que Manual | O mesmo que Manual. Cada lote de edições é mostrado como uma alteração revisável antes de ser aplicado | Ler segredos armazenados |
| Plano | Ler e listar arquivos, e comandos seguros | Nada: ele só lê e propõe um plano | Toda alteração, toda ação de rede |
| Auto | Ler, criar e editar arquivos, comandos, ações de rede e do navegador, e provedores de modelos | Excluir, sobrescrever arquivos existentes, instalar dependências, alterar configurações de provedores, commit, push, merge e reset do Git, implantação, envio de mensagens, pagamentos e alteração de configurações | Ler segredos armazenados, alterar configurações de segurança e qualquer ação cujo efeito o VOOL não consiga identificar |
| Bypass de permissões | Tudo, exceto as quatro classes protegidas | Pagamentos | Ler segredos armazenados, alterar configurações de segurança e qualquer ação cujo efeito o VOOL não consiga identificar |

Um modo, uma habilidade ou um plugin só podem restringir o que é oferecido. Nenhum deles concede uma ação que o
modo não permita.

## Responder a um pedido de permissão

Quando uma ação precisa de você, uma barra **Permissão necessária** se abre acima da caixa de mensagem. Ela mostra
a ação exata: a linha de comando que vai rodar, ou os arquivos que vão mudar, se a
alteração pode ser desfeita, e uma prévia das edições. Nada roda até você responder, e o pedido
espera por você mesmo que o VOOL reinicie. Um pedido cujo tempo acabou aparece como expirado e não pode
ser aprovado.

A barra oferece só as opções que se aplicam à ação:

| Botão | O que permite |
| --- | --- |
| Permitir uma vez | Esta única ação exata. |
| Permitir para esta tarefa | A mesma ação exata de novo durante esta tarefa, no mesmo modo. Oferecido para ações que não são edições de arquivos. |
| Permitir as *N* alterações planejadas para esta solicitação | As alterações de arquivos que este pedido já planejou, exatamente os arquivos listados em Revisar detalhes, e nada mais. |
| Permitir edições do espaço de trabalho nesta conversa | Leituras, arquivos novos e edições dentro do espaço de trabalho desta conversa, por oito horas ou até o VOOL reiniciar ou a conversa ser excluída. |
| Permitir neste projeto | Leituras e gravações de arquivos dentro da pasta do projeto. Isso vale para o projeto, também depois de reiniciar e em conversas novas. |
| Revisar detalhes | Mostra o pedido completo antes de você decidir. |
| Negar | A ação não roda. |

Seja o que for que você permita, exclusões, movimentações, sobrescritas, comandos, ações de rede, segredos e
pagamentos continuam perguntando separadamente a cada vez, a menos que o próprio modo os permita.

## Bypass de permissões

O bypass roda as ferramentas disponíveis sem perguntas individuais, dentro de um escopo e por um tempo que
você escolhe:

* **Escopo:** somente a tarefa atual, esta sessão de chat ou este projeto.
* **Duração:** 15 ou 30 minutos, 1, 2, 4 ou 8 horas, uma duração personalizada de 15 a 1440 minutos,
  ou *Até eu desligar*, somente para esta conversa.

Um bypass temporizado funciona em qualquer conversa. *Até eu desligar* exige que a conversa esteja vinculada a
uma pasta de projeto, e essa pasta não pode ser a sua pasta pessoal inteira, a raiz do disco nem uma pasta que
contenha os dados do próprio VOOL. Enquanto o bypass está ligado, um aviso mostra o escopo dele e quando ele
termina, e o **×** dele o desliga na hora; a conversa volta para Manual. Excluir a conversa também encerra o
bypass dela. O bypass nunca concede acesso fora do espaço de trabalho e não pode deixar um modelo ampliar as
próprias permissões. Pagamentos continuam perguntando, e segredos e configurações de segurança continuam fora
de alcance.

## Pagamentos e gastos

Aprovar uma ação nunca aprova um pagamento. Os pagamentos passam pela proposta e pela aprovação próprias da
carteira, com os limites dela, em todos os modos. As chamadas de modelos pagos são retidas pelos seus limites de
gastos e proteções de preço antes de o provedor ser contatado. Veja [Carteira](../guides/wallet.md) e
[Limites de gastos e proteções de preço](../guides/spending-limits.md).

## Quando uma ação é recusada

Uma ação recusada não alterou nada, e a mensagem diz o que foi recusado e por quê. Os códigos
que você pode ver são [`permission_denied`](../ERROR_BOOK.md#permission-denied),
[`confinement_refusal`](../ERROR_BOOK.md#confinement-refusal) para uma alteração fora da pasta
permitida, e [`tool_unavailable`](../ERROR_BOOK.md#tool-unavailable) para uma ferramenta que este modo não
oferece. Veja [Erros e códigos de erro](../reference/errors.md).
