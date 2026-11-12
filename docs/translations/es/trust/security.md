---
description: Cómo protege VOOL tus archivos y tus claves API: los límites del espacio de trabajo, el almacenamiento de credenciales y a qué puede acceder un plugin.
translation: machine
source_sha256: 4397ca0e7b494efacb90aa546eae10800b9a58558ba7c611c0131f4410c2fe3a
---

# Modelo de seguridad

## Límites

* Los **límites de carpetas** acotan el acceso a los archivos. VOOL lee y cambia archivos en la carpeta del
  proyecto del chat. Fuera de ella, VOOL puede leer las carpetas visibles de tu carpeta personal y cambiar archivos
  solo en Escritorio, Descargas y Documentos; las carpetas ocultas, los datos de apps y del sistema y los datos
  de VOOL nunca se leen. Consulta [Espacios de trabajo](../concepts/workspaces.md).
* El **almacén de credenciales** guarda las claves. Las claves no se escriben en los archivos de configuración ni
  en los registros.
* La **confirmación de herramientas** acota los cambios. En el modo Manual, cada cambio pregunta antes; en el modo
  Auto, las ediciones y los comandos habituales pueden ejecutarse automáticamente, mientras que eliminar,
  sobrescribir, instalar, gastar y cambiar ajustes siguen preguntando. Consulta [Herramientas](../concepts/tools.md).

## Tu red local

De forma predeterminada, VOOL solo escucha en este ordenador. Cuando lo configuras para escuchar en tu red
local, solo este ordenador se trata como tú. Los ajustes, los archivos, las claves, las herramientas y las
funciones privadas solo le responden a él.

Otro dispositivo de la red obtiene el chat básico. También puede leer una página web pública cada vez.
Esa página debe estar en la internet pública: las direcciones de este ordenador o de tu red
local se rechazan, y cada redirección se comprueba de la misma forma. Las búsquedas web, la investigación,
el navegador, las cotizaciones, los pagos y tus claves de pago solo funcionan en este ordenador.

## Puente de Discord

El puente de Discord solo responde a su propietario. El primer usuario de Discord que le envía un comando
se convierte en el propietario. Para elegir tú el propietario, define `VOOL_DISCORD_OWNER_USER_ID` con tu
ID de usuario de Discord; hazlo en cualquier canal en el que otras personas puedan escribir. A todos los demás
se les dice una vez que este VOOL está bloqueado para su propietario, y sus mensajes no se procesan. Incluso los
mensajes de Discord del propietario tienen los mismos límites que otro dispositivo de la red.

## Claves

VOOL guarda tus claves de proveedor en un archivo cifrado en su runtime home en este ordenador
(consulta [Configuración](../reference/configuration.md)), que solo puede leer tu cuenta de usuario.
Las claves que VOOL ya guardaba en el llavero de macOS en un Mac se siguen pudiendo leer ahí. Una clave se usa
desde tu equipo para llegar a tu proveedor. No se envía a Parad0x Labs, y no hay ningún
endpoint operado por Parad0x en la ruta de la petición.

{% hint style="info" %}
El archivo cifrado está protegido por los permisos de archivo de tu cuenta de usuario, no por una contraseña
aparte. Mantén activado el cifrado del disco (FileVault en un Mac) para que un disco perdido o robado no
exponga tus claves.
{% endhint %}

## Plugins

Un plugin se ejecuta con el acceso del entorno de ejecución. Eso es lo que hace útiles a los plugins y lo que hace
que merezca la pena leerlos antes de instalarlos. Consulta [Habilidades y plugins](../guides/skills-and-plugins.md).

## Informar de un problema

Informa de los problemas de seguridad de forma privada a través de
[GitHub Security Advisories](https://github.com/Parad0x-Labs/vool/security/advisories/new),
nunca en una incidencia pública. Incluye la versión, la plataforma y los pasos para reproducirlo.

{% hint style="warning" %}
Las apps para macOS tienen una firma ad hoc y no están notarizadas; el archivo tar para Linux no está firmado.
Consulta [Estado de las versiones](release-status.md).
{% endhint %}
