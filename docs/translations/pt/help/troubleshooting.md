---
description: Soluções para os problemas mais comuns.
translation: machine
source_sha256: 792c90fd68caf7409212d3742109c46e49d1f7e640aa3e9f70db9fe61ca55d77
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

## Começar do zero

Encerre o VOOL e remova o diretório de configuração indicado em
[Configuração](../../../reference/configuration.md). Os arquivos do espaço de trabalho não são alterados.
