---
description: Como conectar o OpenRouter ao VOOL para que uma única chave de API dê acesso a muitos modelos de IA na nuvem.
translation: machine
source_sha256: 3a09fe6c636e2b8dd14a2d627c4653055dbb10bf8fd8221b7a3bcccf3044cb19
---

# Conectar o OpenRouter

O OpenRouter dá acesso a muitos provedores por trás de uma única chave. O VOOL permite conectar uma
conta do OpenRouter.

## Conectar

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

## Desconectar

Remova a chave em **Configurações → Chaves de API**. Revogue também a chave na sua conta do OpenRouter se quiser que ela deixe de funcionar do lado do servidor.
