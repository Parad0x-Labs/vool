---
description: Cómo conectar OpenRouter a VOOL para que una sola clave API dé acceso a muchos modelos de IA en la nube.
translation: machine
source_sha256: 3a09fe6c636e2b8dd14a2d627c4653055dbb10bf8fd8221b7a3bcccf3044cb19
---

# Conectar OpenRouter

OpenRouter da acceso a muchos proveedores con una sola clave. VOOL permite conectar una
cuenta de OpenRouter.

## Conectar

Crea una clave en tu cuenta de OpenRouter, pégala en **Ajustes → Claves API** y pulsa
**Guardar clave**. VOOL comprueba la clave con OpenRouter una vez y después la guarda en un archivo cifrado en
este ordenador (consulta [Modelo de seguridad](../trust/security.md#claves)).

## Facturación y atribución

{% hint style="info" %}
Tu cuenta de OpenRouter sigue siendo tuya. VOOL envía las peticiones directamente desde este ordenador
con tu clave. Parad0x no hace de intermediario de tus prompts ni paga tu uso. El uso real de VOOL
puede aparecer en las estadísticas públicas y agregadas de VOOL en OpenRouter, pero los prompts individuales
y las identidades no se publican.
{% endhint %}

## Desconectar

Quita la clave en **Ajustes → Claves API**. Revoca también la clave en tu cuenta de OpenRouter si quieres que deje de funcionar en el lado del servidor.
