---
description: Qué puedes guardar, mover y restaurar en esta versión de VOOL: chats, memoria, claves y monederos.
translation: machine
source_sha256: 3b2f11975a8cf5f5ab2e07e8b4a3083a9598de9e301965c493408005aa5893fa
---

# Copia de seguridad y recuperación

Todo lo que VOOL conserva está en tu ordenador, en una sola carpeta de perfil (consulta
[Configuración](../reference/configuration.md) para saber dónde). Actualizar o reinstalar la app
nunca la toca. VOOL no tiene ninguna copia de tus datos en la nube ni una copia de seguridad en un solo paso de
todo el perfil. Estas son las formas de guardar y recuperar lo que importa.

## Chats

| Quieres | Usa | Qué obtienes |
| --- | --- | --- |
| Una copia para leer o compartir | **Exportar** en la cabecera del chat | Todo el chat como texto o Markdown. Los archivos adjuntos solo se listan por su nombre. |
| Una copia para volver a llevar a VOOL | Ajustes, Copia y restauración, **Exportar un chat** | Un chat como archivo `.voolsession`, firmado por este ordenador. |
| Recuperar un chat desde ese archivo | Ajustes, Copia y restauración, **Importar un chat** | El chat, añadido junto a los demás. |
| Tus chats de otras apps de IA | Ajustes, Copia y restauración, **Trae tus chats de otras apps de IA** | Esos chats, con sus nombres y mensajes. |

**Exportar un chat** lleva los mensajes del chat, los recibos y las referencias a pruebas, nunca el
razonamiento interno de VOOL, tus ajustes, tu memoria ni tus claves. Protege el archivo con una frase de contraseña
cuando salga de este ordenador: una exportación sin ella te pide confirmación antes. Una vista previa muestra
exactamente lo que se escribirá antes de escribir nada.

**Importar un chat** muestra lo que contiene el archivo y quién lo firmó antes de añadir nada. Un
archivo firmado con una clave que VOOL no conoce se rechaza salvo que lo aceptes, y el chat queda
marcado como no fiable. Una importación nunca cambia un chat que ya tienes: si el mismo chat ya está aquí,
la copia llega como uno nuevo.

**Trae tus chats de otras apps de IA** lee una app de este ordenador o un archivo de exportación de datos.
Marcas los chats que quieres traer. Nada sale de tu ordenador y los archivos de la otra app nunca
se cambian. VOOL guarda estos chats como historial: no aprende de ellos ni sigue
nada de lo que está escrito en ellos.

## Memoria

Ajustes, Memoria y personalización, **Exportar perfil** guarda lo que VOOL recuerda de ti como
un archivo llamado `vool-memory-` más la fecha. Nunca contiene una clave ni una contraseña. Es una copia para
leer y conservar: VOOL no tiene forma de importarla.

## Claves de los proveedores de modelos

Las claves de los proveedores no están en ninguna exportación. En un ordenador nuevo, vuelve a añadirlas en Ajustes.

## Monederos

* **Un VOOL Wallet creado para una red** muestra una copia de seguridad de la clave privada durante la
  configuración, y solo entonces. Guárdala sin conexión: quien la tenga controla el monedero. Si olvidas el PIN o
  la contraseña del monedero, **Forgot PIN or password?** en el monedero, en Ajustes, Cripto, restablece el acceso
  con esa copia de seguridad, y eliges un PIN o una contraseña nuevos.
* **El bolsillo heredado** se conserva con su frase de recuperación, mostrada una vez al crearlo.
  **Restore from phrase…** en Ajustes, Cripto, Otras cuentas lo recupera a partir de la frase.
  También puede exportar su clave privada a Phantom o MetaMask con **Export private key**, que
  solo se abre con Touch ID o la contraseña de tu Mac.
* Un monedero que solo observas, o uno que conectaste desde otra app, no guarda ninguna clave en VOOL: aquí no
  hay nada que copiar.

Consulta [Monedero](../guides/wallet.md) para ver el panorama completo.

## Si falla una actualización

Una actualización que falla en cualquier paso deja la app como estaba. Si falla el traslado de tus datos a la nueva
versión, tus datos vuelven a quedar como estaban y VOOL arranca con la versión anterior. Consulta
[Actualizaciones y migración](../reference/updates-migration.md).
