---
description: Soluções para os problemas mais comuns.
translation: machine
source_sha256: 809bae11bc4bf8c2d707cfef616f80056a5d98d35b0786cc937a40526e802689
---

# Solução de problemas

## O macOS se recusa a abrir o app

É esperado: o app ainda não é notarizado. Confira primeiro a soma de verificação e depois abra
**Ajustes do Sistema → Privacidade e Segurança** e clique em **Abrir Mesmo Assim**. No macOS 14, você também
pode usar Controle + clique no VOOL e escolher **Abrir**.

## Windows

Não há download para Windows nesta versão. No Windows, a instalação é a partir do código-fonte e é
experimental; veja o [guia de instalação](../getting-started/install.md#instalar-a-partir-do-código-fonte).

## Nenhum modelo local aparece na lista

1. Confirme que o ambiente de execução está rodando.
2. Confirme que pelo menos um modelo foi baixado.
3. Confirme que a porta corresponde à que o VOOL espera.

```bash
curl http://localhost:11434/api/tags
```

Se isso não retornar nada, o ambiente de execução não está ativo.

## As respostas estão lentas

Geralmente é o tamanho do modelo em relação à memória RAM disponível. Veja
[Executar um modelo local](../../../guides/local-models.md).

## Um erro mostra um código

Toda falha tem um código fixo. [Erros e códigos de erro](../../../reference/errors.md) explica como lê-lo,
e o [Livro de erros](../../../ERROR_BOOK.md) diz, para cada código, o que aconteceu, o que pode já ter
acontecido e o que fazer em seguida.

## Começar do zero

Encerre o VOOL e remova a pasta “runtime home” indicada em
[Configuração](../../../reference/configuration.md). Isso apaga suas conversas, sua memória, suas
configurações, suas chaves salvas e qualquer carteira que o VOOL guarde neste computador; por isso,
salve primeiro o que você precisa: veja [Backup e recuperação](../../../help/backup-and-recovery.md). Os arquivos
do espaço de trabalho não são alterados.
