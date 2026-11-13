---
description: Como executar modelos de IA localmente no seu próprio computador com o VOOL — instalar um servidor de modelos local, escolher o tamanho do modelo e trabalhar totalmente offline.
translation: machine
source_sha256: a19d4790729d13ed5e1b134bcb67ed3e7a80609561be4227ab9480492100c8a6
---

# Executar um modelo local

## Instalar um ambiente de execução

O VOOL usa o Ollama por padrão para rodar modelos locais; outros ambientes de execução locais com um
endpoint compatível com a OpenAI também funcionam. O Ollama é um programa separado, instalado à parte
a partir de [ollama.com](https://ollama.com). O app para macOS inclui o próprio executor de modelos locais,
então em um Mac você pode pular esta etapa. No Linux e nas instalações a partir do código-fonte, instale o
Ollama, inicie-o e baixe um modelo.

```bash
ollama serve
ollama pull qwen3:4b
```

O VOOL escolhe o modelo local para você por padrão — um modelo Qwen do tamanho certo para o seu computador. Em
muitos computadores esse modelo é o `qwen3:4b`; em computadores com 32 GB de memória ou mais, o
padrão é um modelo Qwen maior.

## Apontar o VOOL para ele

Abra **Configurações → Modelos e provedores**. Os ambientes de execução locais em funcionamento são detectados
automaticamente. Selecione um modelo e o indicador de modo muda para Local.

## Deixe-o pronto uma vez

Um modelo local só escreve respostas depois que o VOOL o verifica. Abra
**Configurações → Modelos e provedores → Modelos locais** e pressione **Usar este modelo** ao lado do modelo.
O VOOL verifica discretamente, no seu computador, se o modelo consegue usar as ferramentas do VOOL bem o bastante
para responder por você. A verificação pode levar um ou dois minutos; nada é baixado e ninguém é contatado. Quando
ele passa, o modelo mostra **Pronto** e o VOOL responde com ele. Até lá, o VOOL não deixa que ele escreva
respostas finais.

* **Não é uma boa escolha:** o modelo não consegue usar as ferramentas do VOOL bem o bastante. O VOOL mostra o que você
  pode fazer em vez disso: usar ou baixar o modelo que ele escolheu para este computador, ou usar um modelo na nuvem.
* **A verificação não terminou:** confira se o Ollama está aberto e pressione **Tentar de novo**. Um computador lento
  pode precisar de uma segunda tentativa.

Se nenhum modelo local estiver pronto quando o VOOL iniciar, ele verifica até três dos modelos deste computador,
começando pelo que você escolheu, e usa o primeiro que passar. Ele pula um modelo grande demais para este
computador. Um modelo pronto sempre vem antes de um que não está pronto.

## Tamanho

| Tamanho do modelo | Memória RAM necessária | Indicado para |
| --- | ---: | --- |
| 3B | ~4 GB | Edições curtas, perguntas rápidas |
| 7–8B | ~8 GB | Uso geral, a maior parte da programação |
| 14B | ~16 GB | Raciocínios mais longos |
| 32B+ | 32 GB ou mais | Trabalho mais pesado, mais lento |

{% hint style="info" %}
Versões quantizadas reduzem bastante o uso de memória RAM. Um modelo de 7B em 4 bits roda com folga em cerca
de 5 GB.
{% endhint %}

## Se estiver lento

* Escolha um modelo menor ou mais quantizado.
* Feche outros aplicativos que usam muita memória RAM.
* Encurte o contexto — menos arquivos no pedido significa menos coisa para processar.
