---
description: Un recibo es el registro de lo que una ejecución hizo realmente.
translation: machine
source_sha256: caffc8be79cb1017a81f74baa2451e076db2e6660302b57db4b9d3ec92dab8b8
---

# Recibos

Un recibo es el registro duradero de una ejecución: qué herramientas se llamaron, con qué entradas y
qué devolvió cada una.

## Por qué existen

El resumen que hace un agente de su propio trabajo es una afirmación. El recibo es el registro con el que puedes
comprobar esa afirmación.

## Qué contiene

| Campo | Significado |
| --- | --- |
| `tool` | Qué acción se ejecutó |
| `input` | Qué se le dio |
| `result` | Qué devolvió |
| `time` | Cuándo se ejecutó |

Los recibos se guardan en la carpeta «runtime home» de VOOL en este ordenador (consulta
[Configuración](../reference/configuration.md)), no en las carpetas de tus proyectos. Un recibo solo sale
de tu equipo dentro de un informe de error que previsualizas y decides enviar (consulta
[Informes de errores y privacidad](../trust/bug-reporting.md)).
