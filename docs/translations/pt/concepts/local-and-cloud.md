---
description: Quais dados saem do seu computador quando você usa o VOOL, e quando. A diferença entre executar a IA localmente e usar um modelo na nuvem.
translation: machine
source_sha256: 2c2b9e26e99579c00a032dfeac95fd216fe9229497c965c1bc08aface7a6c071
---

# Local e nuvem

O VOOL pode responder de duas formas: neste computador ou por meio de um provedor na nuvem. O cabeçalho
da conversa mostra de que forma o próximo pedido é executado — por exemplo, **Auto** com
**Local primeiro** quando o VOOL Auto responde a partir deste computador.

## Local

O modelo e os seus arquivos ficam neste computador. O modelo roda em um ambiente de execução local, os arquivos são lidos
do disco e nenhum pedido é feito a um provedor. Fatos ao vivo, como clima
ou preços, vêm de serviços web públicos somente enquanto Pesquisas na web estiver ativado (fica desativado em uma instalação nova), e nunca em Somente local.

## Nuvem (nome do provedor)

O contexto selecionado é enviado diretamente ao provedor que você selecionou, a partir da sua máquina,
usando a sua chave.

“O contexto selecionado” é o que o app reuniu para aquele pedido: o seu prompt, mais
os arquivos e os turnos anteriores de que o pedido precisa.

{% hint style="danger" %}
O modo nuvem envia dados para fora da sua máquina. É para isso que o modo nuvem existe. O que o VOOL afirma diz
respeito à *rota* — o pedido vai do seu computador para o seu provedor —, não que
nada seja transmitido.
{% endhint %}

## O que fica no meio

Nada operado pela Parad0x Labs. Não há proxy, gateway nem retransmissor para o tráfego dos modelos.
A sua chave é usada pelo app na sua máquina para falar diretamente com o provedor.

Todos os detalhes: [Tratamento de dados](../trust/data-handling.md).
