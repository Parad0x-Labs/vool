---
description: Los cinco modos, qué deja hacer cada uno a VOOL sin preguntar y qué cubre cada botón de aprobación.
translation: machine
source_sha256: 1f33c8d63bcdeddbd1ac44d3b2d948b354137954ee6accb6ee18eeec8bdda1c6
---

# Permisos

VOOL decide qué puede ejecutarse antes de que se ejecute nada. El modelo puede pedir una acción; el modo que
elegiste y tus respuestas deciden si ocurre. Leer se trata de forma distinta a
cambiar, y los secretos, los pagos y los ajustes de seguridad están protegidos en todos los modos.

## Los modos

Elige el modo en el menú junto al cuadro de mensaje. Un chat nuevo empieza en **Manual**.

| Modo | Se ejecuta sin preguntar | Pregunta antes | Nunca |
| --- | --- | --- | --- |
| Manual | Leer y listar archivos, comandos seguros y consultas web públicas de solo lectura | Cada cambio: ediciones de archivos, comandos, acciones de red, ajustes, Git, despliegue y mensajes | Leer secretos guardados |
| Revisar ediciones | Lo mismo que Manual | Lo mismo que Manual. Cada lote de ediciones se muestra como un cambio revisable antes de aplicarse | Leer secretos guardados |
| Plan | Leer y listar archivos, y comandos seguros | Nada: solo lee y propone un plan | Todo cambio y toda acción de red |
| Auto | Leer, crear y editar archivos, comandos, acciones de red y del navegador, y proveedores de modelos | Eliminar, sobrescribir archivos existentes, instalar dependencias, cambiar los ajustes de los proveedores, commit, push, merge y reset de Git, despliegue, enviar mensajes, pagos y cambiar ajustes | Leer secretos guardados, cambiar ajustes de seguridad y cualquier acción cuyo efecto VOOL no pueda identificar |
| Bypass de permisos | Todo excepto las cuatro clases protegidas | Pagos | Leer secretos guardados, cambiar ajustes de seguridad y cualquier acción cuyo efecto VOOL no pueda identificar |

Un modo, una habilidad o un plugin solo pueden restringir lo que se ofrece. Ninguno de ellos concede una acción que el
modo no permita.

## Responder a una solicitud de permiso

Cuando una acción te necesita, se abre una barra **Permiso necesario** encima del cuadro de mensaje. Indica
la acción exacta: la línea de comandos que se ejecutará o los archivos que cambiarán, si el
cambio se puede deshacer, y una vista previa de las ediciones. No se ejecuta nada hasta que respondes, y la solicitud
te espera aunque VOOL se reinicie. Una solicitud cuyo tiempo se agotó aparece como caducada y no se puede
aprobar.

La barra ofrece solo las opciones que encajan con la acción:

| Botón | Qué permite |
| --- | --- |
| Permitir una vez | Esta única acción exacta. |
| Permitir para esta tarea | La misma acción exacta otra vez durante esta tarea, en el mismo modo. Se ofrece para acciones que no son ediciones de archivos. |
| Permitir los *N* cambios planificados para esta solicitud | Los cambios de archivos que esta solicitud ya ha planificado, exactamente los archivos listados en Revisar detalles, y nada más. |
| Permitir ediciones del espacio de trabajo en este chat | Lecturas, archivos nuevos y ediciones dentro del espacio de trabajo de este chat, durante ocho horas o hasta que VOOL se reinicie o se elimine el chat. |
| Permitir en este proyecto | Lecturas y escrituras de archivos dentro de la carpeta del proyecto. Se mantiene para el proyecto, también después de reiniciar y en chats nuevos. |
| Revisar detalles | Muestra la solicitud completa antes de que decidas. |
| Denegar | La acción no se ejecuta. |

Permitas lo que permitas, las eliminaciones, los movimientos, las sobrescrituras, los comandos, las acciones de red,
los secretos y los pagos siguen preguntando por separado cada vez, salvo que el propio modo los permita.

## Bypass de permisos

El bypass ejecuta las herramientas disponibles sin avisos individuales, dentro de un ámbito y durante un tiempo que
tú eliges:

* **Ámbito:** solo la tarea actual, esta sesión de chat o este proyecto.
* **Duración:** 15 o 30 minutos, 1, 2, 4 u 8 horas, una duración personalizada de 15 a 1440 minutos,
  o *Hasta que yo lo apague*, solo para este chat.

Un bypass con duración funciona en cualquier chat. *Hasta que yo lo apague* necesita que el chat esté vinculado a una
carpeta de proyecto, y esa carpeta no puede ser toda tu carpeta personal, la raíz del disco ni una carpeta que
contenga los datos de VOOL. Mientras el bypass está activo, un aviso muestra su ámbito y cuándo termina, y su
**×** lo desactiva al instante; el chat vuelve a Manual. Eliminar el chat también termina su bypass.
El bypass nunca concede acceso fuera del espacio de trabajo y no puede permitir que un modelo amplíe sus propios
permisos. Los pagos siguen preguntando, y los secretos y los ajustes de seguridad siguen fuera de su alcance.

## Pagos y gastos

Aprobar una acción nunca aprueba un pago. Los pagos pasan por la propuesta y la aprobación propias del
monedero, con sus límites, en todos los modos. Las llamadas a modelos de pago quedan retenidas por tus límites de
gasto y tus protecciones de precio antes de contactar con el proveedor. Consulta [Monedero](../guides/wallet.md) y
[Límites de gasto y protecciones de precio](../guides/spending-limits.md).

## Cuando se rechaza una acción

Una acción rechazada no ha cambiado nada, y el mensaje indica qué se rechazó y por qué. Los códigos
que puedes ver son [`permission_denied`](../ERROR_BOOK.md#permission-denied),
[`confinement_refusal`](../ERROR_BOOK.md#confinement-refusal) para un cambio fuera de la carpeta
permitida, y [`tool_unavailable`](../ERROR_BOOK.md#tool-unavailable) para una herramienta que este modo no
ofrece. Consulta [Errores y códigos de error](../reference/errors.md).
