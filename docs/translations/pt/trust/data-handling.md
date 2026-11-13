---
description: Exatamente quais dados o VOOL envia, quando e para quem. O que fica no seu computador e o que chega ao seu provedor de IA.
translation: machine
source_sha256: 4fd0aaa4d2480ae80b94aa7132c46c8263113450e4b02c5747388791e7bc8710
---

# Tratamento de dados

## Os dois estados

**Local** — o modelo e os seus arquivos ficam neste computador. Fatos ao vivo, como
clima ou preços, vêm de serviços web públicos somente enquanto Pesquisas na web estiver ativado (fica desativado em uma instalação nova), e nunca em Somente local.

**Nuvem** — o contexto selecionado é enviado diretamente ao provedor selecionado.

O cabeçalho da conversa mostra de que forma o próximo pedido é executado — por exemplo, **Auto** com
**Local primeiro** quando o VOOL Auto responde a partir deste computador.

## O que o VOOL não faz

* Ele não passa os seus prompts pela infraestrutura da Parad0x Labs.
* Ele não envia o seu espaço de trabalho para a internet.
* Ele não envia a sua chave a lugar nenhum, exceto ao provedor a que ela pertence.

## O OpenRouter em particular

A sua conta do OpenRouter continua sendo sua. O VOOL envia os pedidos diretamente deste computador
usando a sua chave. A Parad0x não intermedia os seus prompts nem paga pelo seu uso. O uso real do VOOL
pode aparecer nas estatísticas públicas e agregadas do VOOL no OpenRouter, mas prompts individuais
e identidades não são publicados.

## Telemetria

O VOOL não envia telemetria de produto. Um relatório de bug só sai da sua máquina quando você o visualiza
e escolhe enviá-lo; veja [Relato de bugs e privacidade](bug-reporting.md).

## Valem os termos do seu provedor

No modo nuvem, os seus dados chegam ao seu provedor sob os termos de retenção e treinamento **dele**,
não os do VOOL. Leia os termos do provedor que você escolher.
