---
description: Como usar o VOOL como assistente de IA para programação no seu próprio código — definir o espaço de trabalho, perguntar sobre o código e revisar as edições.
translation: machine
source_sha256: 1d1c4e7c4222eed4dc6caae24dca70729496ead8b88b1fc45a81a61ed548241a
---

# Programar com o VOOL

## Use o repositório como espaço de trabalho

Aponte o [espaço de trabalho](../concepts/workspaces.md) para a raiz do projeto. O VOOL lê os
arquivos de que precisa em vez de carregar a árvore inteira.

## Pergunte em termos do código

Citar um arquivo ou um símbolo dá ao ambiente de execução um ponto de partida:

> Encontre onde o tempo limite da sessão é definido em `src/auth/` e explique o que acontece quando ele
> expira.

## Revise antes de aplicar

No modo Manual, toda edição pergunta antes. No modo Auto, as edições e os comandos do dia a dia podem rodar
automaticamente e aparecem no registro de atividade, enquanto exclusões e sobrescritas continuam perguntando.
Faça um commit antes de uma alteração grande para que o diff seja fácil de ler e fácil de desfazer.

## Local ou nuvem

Programar funciona nos dois modos. Um modelo local de programação de 7B dá conta da maioria das edições do dia a
dia; modelos maiores na nuvem dão conta de raciocínios mais longos. O indicador de modelo diz qual está ativo antes
de você enviar: `LOCAL` para um modelo local, `CLOUD` para um na nuvem e, por padrão, `AUTO`
(`VOOL Auto`, `Local first`) quando o VOOL escolhe.
