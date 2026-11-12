---
description: VOOL é um assistente de IA para desktop que executa modelos de IA localmente no seu computador ou se conecta a modelos na nuvem com a sua própria chave. Gratuito e privado. Downloads para Mac (Apple Silicon e Intel) e Linux; o Windows é experimental e instala a partir do código-fonte.
translation: machine
source_sha256: c31e5a047068e09c01bbd2e2c90cbbed0b70e78926847a5b737f9e0e625ab1dd
---

# VOOL

VOOL é um assistente de IA para desktop. Ele é instalado como um aplicativo no seu computador e pode
executar modelos de IA localmente nessa máquina ou se conectar a um provedor de IA na nuvem usando a sua
própria chave de API. Ele mantém um registro legível de cada ação que realiza.

Ele é gratuito e funciona offline com modelos locais. Há downloads para Mac (Apple Silicon
e Intel) e Linux x86_64; o Windows é experimental e instala a partir do código-fonte.

Esta documentação mostra como instalar o VOOL e conectar um modelo, e explica os conceitos por trás de como ele
trata os seus arquivos, a sua memória e as suas chaves.

## Como ele se sai

Em 150 perguntas de memória do LoCoMo nunca usadas antes, com o mesmo modelo respondendo por todos os agentes e o
mesmo avaliador GPT-4o, a memória do VOOL superou dois outros agentes de código aberto:

| | VOOL | OpenClaw | Hermes |
| --- | --- | --- | --- |
| Respostas certas | **82,7%** (124 / 150) | 71,3% (107) | 62,7% (94) |
| Tokens por pergunta | **9,6k** | 51,8k | 33,5k |
| Custo por pergunta | **US$ 0,0033** | US$ 0,0100 | US$ 0,0046 |

São 5,4 vezes menos tokens que o OpenClaw e 3,5 vezes menos que o Hermes, com um terço do custo por resposta
do OpenClaw. A memória incluída no VOOL 0.7 acertou 123 de 150 nas mesmas perguntas.
Cada execução, a configuração e os pontos fracos estão publicados: [benchmarks de memória](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

## Comece aqui

| Se você quer | Leia |
| --- | --- |
| Colocar o VOOL para funcionar | [Instalação](getting-started/install.md) |
| Entender a primeira tela | [Primeira execução](getting-started/first-run.md) |
| Apontá-lo para um modelo | [Conectar um modelo](getting-started/connect-a-model.md) |
| Controlar quais gastos podem acontecer | [Limites de gastos](../../guides/spending-limits.md) |
| Saber o que sai da sua máquina | [Local e nuvem](../../concepts/local-and-cloud.md) |

## O que o VOOL é

* **Um aplicativo para desktop.** Ele é instalado no seu computador — não é uma aba do navegador nem um
  serviço hospedado.
* **Funciona offline.** Com um modelo local selecionado, o VOOL não precisa de conexão com a internet.
* **Local por padrão.** Com um modelo local selecionado, os prompts e os arquivos são processados
  na sua máquina.
* **Independente de modelo.** Tanto ambientes de execução locais quanto provedores na nuvem podem ser
  selecionados. Você fornece a chave da nuvem, e ela fica na sua máquina.
* **Inspecionável.** Cada chamada de ferramenta é registrada com suas entradas e resultados, para que uma
  execução possa ser relida depois.

{% hint style="warning" %}
O VOOL está em beta para Macs com Apple Silicon, Macs com Intel e Linux x86_64. Os apps para Mac
ainda não são notarizados pela Apple, então o macOS pede que você confirme na primeira vez que abrir um deles;
a página de [Instalação](getting-started/install.md#primeira-abertura-em-um-mac) mostra como. Veja o
[Status das versões](../../trust/release-status.md) antes de instalar.
{% endhint %}

## Dois modos, sem rodeios

O VOOL distingue exatamente dois estados, e a interface sempre mostra qual deles está ativo:

1. **Local** — o trabalho fica neste computador.
2. **Nuvem** — o contexto selecionado é enviado diretamente ao provedor que você escolheu,
   a partir da sua máquina, usando a sua chave.

Não existe um terceiro estado, e não há nenhum retransmissor operado pela Parad0x no meio. Os detalhes
estão em [Tratamento de dados](trust/data-handling.md).

## Como obter ajuda

* [Perguntas frequentes](../../help/faq.md) — as dúvidas mais comuns
* [Solução de problemas](help/troubleshooting.md) — quando algo não inicia
* [Discord](https://discord.gg/V9NkjP3Fzz) — pergunte à equipe
