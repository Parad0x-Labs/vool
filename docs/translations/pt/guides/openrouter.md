---
description: Como conectar o OpenRouter ao VOOL para que uma única chave de API dê acesso a muitos modelos de IA na nuvem.
translation: machine
source_sha256: 75cb20d43eb37adb1d30913959ca1cb46864ad57ba7a1a22ec261f0e0eaf2e9a
---

# Conectar o OpenRouter

O OpenRouter dá acesso a muitos provedores por trás de uma única chave. O VOOL permite adicionar uma
chave do OpenRouter.

## Adicionar a chave

Crie uma chave na sua conta do OpenRouter, cole-a em **Configurações → Chaves de API** e pressione
**Salvar chave**. O VOOL verifica a chave com o OpenRouter uma vez e depois a guarda em um arquivo criptografado
neste computador (veja [Modelo de segurança](../trust/security.md#chaves)).

## Cobrança e atribuição

{% hint style="info" %}
A sua conta do OpenRouter continua sendo sua. O VOOL envia os pedidos diretamente deste computador
usando a sua chave. A Parad0x não intermedia os seus prompts nem paga pelo seu uso. O uso real do VOOL
pode aparecer nas estatísticas públicas e agregadas do VOOL no OpenRouter, mas prompts individuais
e identidades não são publicados.
{% endhint %}

## Remover a chave

Remova a chave em **Configurações → Chaves de API**. Revogue também a chave na sua conta do OpenRouter se quiser que ela deixe de funcionar do lado do servidor.
