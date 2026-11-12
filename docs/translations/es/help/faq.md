---
description: Preguntas habituales sobre VOOL: si funciona sin conexión, dónde se guarda tu clave API, si es gratuito y qué sistemas admite.
translation: machine
source_sha256: d98db6dcf1791e9de7aed2dbb7204ab774afd9ea47d883bc0211640a8895413e
---

# Preguntas frecuentes

## ¿Funciona VOOL sin conexión?

Sí, con un modelo local. Los modelos en la nube necesitan conexión a la red por definición.

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
