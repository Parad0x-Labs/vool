---
description: Cómo se actualiza VOOL, qué pasa con tus datos y la compatibilidad entre versiones.
translation: machine
source_sha256: 3b6a950556f4669f116dfa3a911ffb5e07ad8bcfa04415d68cf5a3d1adf0fe65
---

# Actualizaciones y migración

## Cómo funcionan las actualizaciones

A partir de 0.7.5, VOOL se actualiza solo: con «Conectarse por su cuenta» activado, muestra un aviso e instala una nueva versión con una sola pulsación. Si actualizas desde 0.7.4 o 0.7.3 en un Mac: activa «Conectarse por su cuenta» (Ajustes > Privacidad y permisos) y pulsa «Buscar actualizaciones» una vez. Desde 0.7.4 o anterior en Linux, o desde 0.7.2 y anteriores en un Mac: descarga 0.7.7 e instálala sobre tu versión actual. Tus chats y ajustes están en tu perfil, fuera de la app.

Con «Conectarse por su cuenta» activado, VOOL busca una nueva versión al iniciarse y una vez al
día, y muestra un aviso sin que tengas que pulsar «Buscar actualizaciones». Con la opción desactivada, VOOL no
descarga nada y la cabecera muestra **Búsqueda de actualizaciones desactivada**.

Un paquete de actualización firmado pasa por un proceso verificado con reversión automática:

1. **Descarga**: se verifica con un manifiesto firmado.
2. **Instalación preparada**: la nueva versión se instala junto a la anterior.
3. **Migración**: tus datos se migran a la nueva versión del almacén. Si la migración falla, tus
   datos se restauran a la versión anterior y la app se reinicia con ella: *«Your data was
   restored to the previous version.»*
4. **Comprobación de estado**: la nueva versión debe demostrar que puede funcionar antes de retirar la anterior.
5. **Intercambio**: atómico; la versión anterior se conserva como destino de la reversión.

En Linux, la instalación con el archivo tar se actualiza de la misma forma. La nueva versión se instala junto a
la que está en ejecución, y `current` pasa a ella en un solo paso. Si la nueva versión no
funciona bien, VOOL vuelve por sí solo a la anterior. En el Mac y en las instalaciones desde el código fuente
puedes volver atrás manualmente con **Volver a** la versión anterior, pero todavía no en la instalación con el
archivo tar de Linux.

Si alguna fase falla, la app se queda como estaba: el mensaje de error nombra la fase
(`verification_failed`, `insufficient_disk_space`, …) y cada fase tiene su propia frase
de recuperación.

## Compatibilidad de los datos

* Tu perfil, tus chats, tus recibos y tus ajustes están fuera del paquete de la app, así que sustituir la
  app nunca sustituye tus datos.
* El almacén local se niega a abrirse **en una versión anterior**: una base de datos escrita por un VOOL más
  nuevo no la lee en silencio uno más antiguo; lo indica y se niega, porque adivinar una estructura de almacén
  desconocida la corrompería. Vuelve a instalar la versión más nueva para leer los datos nuevos.
* Los archivos instalados por las actualizaciones y el propio estado de la actualización se registran en tu perfil
  con recibos, así que la pregunta «qué versión tocó mis datos» tiene respuesta después.

## Canal beta

La línea de versiones actual es una **beta** (consulta [Estado de las versiones](../trust/release-status.md)).
Las actualizaciones están firmadas con la clave fijada del editor y se comprueban antes de instalarse. La app
para macOS en sí tiene una firma ad hoc y no está notarizada, así que macOS puede pedirte que confirmes el primer
inicio de una nueva versión.
