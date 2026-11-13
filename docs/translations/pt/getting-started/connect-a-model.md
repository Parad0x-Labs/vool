---
description: Como conectar um modelo de IA ao VOOL: um modelo local rodando no seu próprio computador ou um provedor na nuvem usando a sua própria chave de API.
translation: machine
source_sha256: 53f84267e6c9d76240039e6119dfb654fc0e2f479f3bd293ad48232d2df0dd01
---

# Conectar um modelo

O VOOL não vem com nenhum modelo. Ele escolhe um modelo local por padrão, e você pode escolher outro;
essa escolha determina se um pedido fica na sua máquina ou sai dela.

## Local

Se um ambiente de execução local estiver instalado e em funcionamento, o VOOL o detecta e lista os modelos dele.
Ao selecionar um, o indicador passa a mostrar Local. Os detalhes de configuração estão em
[Executar um modelo local](../guides/local-models.md).

## Nuvem

Adicione uma chave de provedor em **Configurações → Chaves de API**. A chave fica guardada em um arquivo criptografado
neste computador (veja [Modelo de segurança](../trust/security.md#chaves)) e é usada para fazer os pedidos
**da sua máquina diretamente ao provedor**.

Ao selecionar um modelo na nuvem, o indicador passa a mostrar o nome do provedor, e ele muda antes de
qualquer pedido ser feito.

Veja [Conectar o OpenRouter](../guides/openrouter.md) para o fluxo específico do OpenRouter.

## Trocar de modelo

Trocar de modelo troca de modo. O indicador é a referência: se ele mostra Local,
o próximo pedido é tratado neste computador.
