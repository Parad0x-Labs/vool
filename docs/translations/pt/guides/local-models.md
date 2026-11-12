---
description: Como executar modelos de IA localmente no seu próprio computador com o VOOL — instalar um servidor de modelos local, escolher o tamanho do modelo e trabalhar totalmente offline.
translation: machine
source_sha256: d93b9995b0f4fec8aca12bb90d8d6f9ff0fd9623c43d4529913dc76f055ac24c
---

# Executar um modelo local

## Instalar um ambiente de execução

O VOOL conversa com um servidor de modelos local. O app para macOS inclui o próprio executor de modelos locais,
então em um Mac você pode pular esta etapa. No Linux e nas instalações a partir do código-fonte, instale um
servidor como o Ollama, inicie-o e baixe um modelo.

```bash
ollama serve
ollama pull qwen2.5-coder:7b
```

## Apontar o VOOL para ele

Abra **Configurações → Modelos e provedores**. Os ambientes de execução locais em funcionamento são detectados
automaticamente. Selecione um modelo e o indicador de modo muda para Local.

## Certifique-o uma vez

Um modelo local só escreve respostas depois de ser certificado. Abra **Configurações → Modelos e provedores → Modelos locais**,
registre o modelo e depois escolha **Certify**. A certificação executa uma verificação rápida de ferramentas na
sua máquina; nada é baixado e ninguém é contatado. Até um modelo passar, o VOOL não
deixa que ele escreva respostas finais.

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
