---
description: Limites de gastos, proteções de preço e o que acontece quando uma chamada paga é interrompida.
translation: machine
source_sha256: a4a5246059942702330cbfc18e9872dc5b0f46192fa5b852bfc9060ca987eae6
---

# Limites de gastos e proteções de preço

O VOOL nunca gasta dinheiro por você sem um teto explícito que você controla. Esta página
descreve as camadas que protegem as chamadas de modelos pagos e os recursos pagos, e o que acontece quando uma
chamada paga é interrompida no meio.

## Dois preços, uma regra

* **Preço de catálogo** — o que um provedor anuncia no momento para um modelo. O VOOL o descobre; é
  informação, não permissão.
* **O seu preço aceito** — o teto que você aprovou para aquele provedor e modelo em Configurações.
  Um preço descoberto acima do seu teto é recusado **antes** de o provedor ser contatado. A
  recusa diz exatamente isso: o modelo não rodou, nada foi enviado, nada foi cobrado.

## Limites de gastos

Para chamadas pagas, o VOOL reserva dinheiro contra os limites antes de cada chamada:

| Limite | Significado |
| --- | --- |
| Por chamada | O teto do custo previsto de uma única chamada. |
| Por tarefa | O máximo que uma tarefa pode gastar em todas as suas chamadas. |
| Diário / mensal | Tetos do período inteiro para o uso pago. |

Uma chamada que ultrapassaria um limite é recusada antes de ser enviada. A recusa cita o limite que
a recusou (`per_task_spend_cap_exceeded` e os códigos semelhantes) e o texto é deliberadamente
de antes do envio: *“running it would exceed your spend cap”* — nunca uma afirmação de que o provedor foi
contatado.

## Um modelo fixado nunca é substituído em silêncio

Se você escolhe explicitamente um modelo pago e uma proteção o recusa, o VOOL **não** responde discretamente
com um modelo mais barato. O turno falha com o nome do modelo que você escolheu, o nome da barreira real
e a frase *“no other model was substituted.”* Escolha você mesmo outro modelo, ou aumente
o limite deliberadamente.

## Pago, mas a resposta nunca chegou

Se uma chamada paga foi cobrada mas a resposta dela não chegou, o resultado é **desconhecido** — o VOOL
não vai fingir que ela falhou ou deu certo:

* Nenhuma resposta é inventada no lugar dela, e nenhum reembolso é presumido.
* O pagamento fica registrado como não resolvido.
* As Configurações o listam em **Paid calls waiting for an answer**, onde **Resume** pede a
  mesma resposta uma vez, **sem pagar de novo**.

É por isso que tentar de novo às cegas nunca é sugerido nesses casos: um simples reenvio poderia pagar
duas vezes. Resume é o único caminho suportado.

## Pagamentos automáticos pela carteira (x402)

Sites que pedem pagamento x402 em USDC na Base ou na Base Sepolia podem ser pagos pela sua
carteira Crypto Pilot, com uma aprovação por PIN dentro desses limites; nenhum pagamento real na mainnet foi
feito com isso ainda. A opção de pagamento por chamada do marketplace de modelos nas Configurações paga pela
carteira Solana Mainnet, com uma aprovação por chamada dentro desses limites, e ainda não foi
testada em uma cadeia real.
Os recursos pagos pela carteira (veja [Carteira](../guides/wallet.md)) têm o próprio limite de pagamento
automático. Um recurso que pede mais do que o limite simplesmente não é pago; você pode aumentar o limite
deliberadamente ou pagar o recurso por meio de uma proposta explícita com a sua aprovação.
