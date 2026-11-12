---
description: Quais dados saem do seu computador quando você usa o VOOL, e quando. A diferença entre executar a IA localmente e usar um modelo na nuvem.
translation: machine
source_sha256: 3217e13bf6d684853b92d19097627d07e64bdd17745560b1204871cb0b7b0753
---

# Local e nuvem

O VOOL tem dois estados. A interface sempre mostra qual deles está ativo.

## Local

Tudo fica neste computador. O modelo roda em um ambiente de execução local, os arquivos são lidos
do disco e nenhum pedido é feito a um provedor.

## Nuvem (nome do provedor)

O contexto selecionado é enviado diretamente ao provedor que você selecionou, a partir da sua máquina,
usando a sua chave.

“O contexto selecionado” é o que o app reuniu para aquele pedido: o seu prompt, mais
os arquivos e os turnos anteriores de que o pedido precisa. Ele é mostrado antes do envio.

{% hint style="danger" %}
O modo nuvem envia dados para fora da sua máquina. É para isso que o modo nuvem existe. O que o VOOL afirma diz
respeito à *rota* — o pedido vai do seu computador para o seu provedor —, não que
nada seja transmitido.
{% endhint %}

## O que fica no meio

Nada operado pela Parad0x Labs. Não há proxy, gateway nem retransmissor para o tráfego dos modelos.
A sua chave é usada pelo app na sua máquina para falar diretamente com o provedor.

Todos os detalhes: [Tratamento de dados](../trust/data-handling.md).
