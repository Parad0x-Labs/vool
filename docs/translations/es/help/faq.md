---
description: Preguntas habituales sobre VOOL: si funciona sin conexión, dónde se guarda tu clave API, si es gratuito y qué sistemas admite.
translation: machine
source_sha256: 4366d0e907cc16c3206be768f984f5a525f4187024d16aae541a2ec8937e0aa0
---

# Preguntas frecuentes

## ¿Funciona VOOL sin conexión?

Sí, con un modelo local, para tus chats y archivos. Los modelos en la nube necesitan conexión
a la red por definición. Los datos en directo como el tiempo o los precios vienen de servicios web públicos solo mientras Búsquedas web está activado (está desactivado en una instalación nueva), y nunca en Solo local.

## ¿Necesito una clave de la nube?

No. Un modelo local basta para usar la app.

## ¿Dónde se guarda mi clave?

En un archivo cifrado en la runtime home de VOOL en tu ordenador, que solo puede leer tu cuenta
de usuario. Las claves que VOOL ya guardaba en el llavero de macOS en un Mac se quedan ahí. Las claves se quedan
en tu equipo. Consulta [Modelo de seguridad](../trust/security.md#claves).

## ¿Ve Parad0x mis prompts?

No en modo local, y tampoco en modo nube: las peticiones a la nube van de tu equipo a
tu proveedor. Consulta [Tratamiento de datos](../trust/data-handling.md).

## ¿Por qué me avisa el sistema al abrirlo?

Las apps para macOS tienen una firma ad hoc, pero aún no están notarizadas, así que macOS te pide que confirmes
la primera apertura. Los pasos están en [Instalación](../getting-started/install.md#primera-apertura-en-un-mac). Consulta
[Estado de las versiones](../trust/release-status.md).

## ¿Puedo usar mi propio modelo?

Sí. Cualquier entorno de ejecución que exponga un endpoint local compatible con OpenAI. Consulta
[Modelos compatibles](../reference/models.md).

## ¿Es gratuito?

La aplicación es gratuita durante la beta. El uso de modelos en la nube lo factura tu
proveedor, a tu cuenta.
