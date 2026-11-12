---
description: As ferramentas são as ações que o VOOL pode realizar, e cada uma delas é registrada.
translation: machine
source_sha256: 53722a7f30f4446b6d029465a402a823f60496a66008b5df150d201c1963b152
---

# Ferramentas

Uma ferramenta é uma ação que o ambiente de execução pode realizar por você — ler um arquivo, executar um
comando, pesquisar em uma pasta.

## Inspecionáveis por design

Cada chamada de ferramenta registra o que foi pedido, o que foi executado e o que voltou. Uma execução pode ser
relida depois sem ser executada de novo.

## Permissão

Se uma ferramenta roda na hora ou pergunta antes depende do modo que você escolheu e da ação.
Uma conversa nova começa em Manual, em que toda alteração pergunta antes. Em Auto, criar e editar
arquivos, comandos e ações de rede rodam sem perguntar, enquanto excluir, sobrescrever,
instalar, commits e pushes do Git, implantação, envio de mensagens, pagamentos e alteração de
configurações continuam perguntando. Em todos os modos, as ferramentas de arquivos só funcionam dentro do
[espaço de trabalho](workspaces.md), as ferramentas de pesquisa e de obtenção de páginas leem recursos públicos
sem gravar nada, e segredos armazenados nunca são lidos. [Permissões](permissions.md) cobre os cinco modos e o
que cada botão de aprovação permite.

```text
tool: read_file
path: src/main.py
result: 412 lines
```

Veja também [Recibos](receipts.md).
