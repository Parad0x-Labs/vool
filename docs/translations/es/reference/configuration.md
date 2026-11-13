---
description: Dónde guarda VOOL su configuración y qué significan los ajustes.
translation: machine
source_sha256: 628a20ac7c08e03575690eb673731afc27ec1196b1514bf1ed75dfb7c21e3daf
---

# Configuración

## Ubicaciones

VOOL guarda sus ajustes, chats, memoria y registros en una sola carpeta, la «runtime home»:

| Instalación | Runtime home |
| --- | --- |
| App para macOS | `~/Library/Application Support/VOOL/runtime` |
| Archivo tar para Linux e instalaciones desde el código fuente | `~/.vool_runtime` |
| Windows (experimental, desde el código fuente) | `%LOCALAPPDATA%\VOOL` |

Si defines `VOOL_HOME` antes de iniciar VOOL, se usa esa carpeta en su lugar. El comando `vool` muestra
la carpeta que se está usando.

Las claves **no** se escriben en los archivos de ajustes ni en los registros. Se guardan cifradas en un archivo de
credenciales aparte en esta runtime home, que solo puede leer tu cuenta de usuario; las claves que VOOL
ya guardaba en el llavero de macOS en un Mac se quedan ahí. Consulta
[Modelo de seguridad](../trust/security.md#claves).

## Ajustes

Cambia los ajustes en la app, en **Ajustes**. Los que deciden qué puede hacer VOOL:

* **Modelo**: el modelo activo, que decide si un turno es LOCAL o NUBE.
* **Espacio de trabajo**: la carpeta del proyecto en la que trabaja un chat; consulta
  [Espacios de trabajo](../concepts/workspaces.md) para ver las demás carpetas a las que VOOL puede acceder.
* **Modo de funcionamiento**: se elige en el menú junto al cuadro de mensaje, no aquí. Manual pregunta
  antes de cada cambio; Auto deja que se ejecuten las ediciones y los comandos habituales, mientras que eliminar,
  sobrescribir, instalar, gastar y cambiar ajustes siguen preguntando. Consulta
  [Permisos](../concepts/permissions.md).
* **Acceso web y criptomonedas**: ambos están desactivados hasta que los activas.

VOOL no tiene ningún ajuste de telemetría del producto porque no envía telemetría del producto.

## Restablecer

Cierra VOOL y aparta la runtime home. Los archivos del espacio de trabajo no se tocan; VOOL vuelve a
arrancar con un perfil nuevo. Tus claves guardadas se apartan con ella, así que vuelve a añadirlas en
**Ajustes → Claves API**. Las claves guardadas en el llavero de macOS se quedan ahí hasta que las eliminas en
Acceso a Llaveros.
