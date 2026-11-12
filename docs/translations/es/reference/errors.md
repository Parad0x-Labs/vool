---
description: Cómo leer un error de VOOL, si ya ha ocurrido algo y dónde se explica cada código.
translation: machine
source_sha256: ccb01d4c778254a055fd67f46241c670aac90cfc83f168b923a19cef1f949de2
---

# Errores y códigos de error

Cuando algo falla, VOOL dice con palabras sencillas qué pasó, qué puede haber pasado ya
y qué hacer a continuación. Cada fallo tiene además un código estable. Lo encontrarás en los detalles técnicos
bajo un error, en la línea de comandos (`wallet_export_refused: …`) y en las respuestas de la API. Esta
página explica cómo leer un error. El [Libro de errores](../ERROR_BOOK.md) explica cada código.

## Leer un error

Comprueba tres cosas antes de volver a intentarlo:

1. **Qué pasó.** El mensaje. Nunca contiene rutas internas ni secretos.
2. **Qué puede haber pasado ya.** La mayoría de los rechazos se detienen antes de enviar o cambiar nada,
   y el mensaje lo dice. Algunos fallos no pueden saber si una acción terminó, como un tiempo de espera
   agotado después de que saliera una petición. Entonces el mensaje dice que el resultado es desconocido y te pide
   que compruebes antes de reintentar. Un pago nunca se envía dos veces por accidente: un segundo envío del
   mismo pago se rechaza con
   [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment).
3. **Qué hacer a continuación.** Un paso concreto.

El código lleva el significado. Un estado HTTP como 403 o 429 solo indica cómo le fue a una petición
por el camino, y dos rechazos distintos pueden compartir un mismo estado. Lee el código, no el estado.

## Los códigos y el Libro de errores

Un código nunca se renombra ni se reutiliza con otro significado. El
[Libro de errores](../ERROR_BOOK.md) se genera a partir del propio catálogo de errores de la app, así que los
códigos, los mensajes y los siguientes pasos que contiene son exactamente lo que muestra la app. Cada código tiene
su propia sección: busca en la página el código tal como te lo dieron, o enlaza a la sección con la
dirección del libro, `#` y el código con los guiones bajos cambiados por guiones, como
[`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed).

Los códigos que es más probable que veas:

| Código | Título | Qué puede haber pasado ya |
| --- | --- | --- |
| [`permission_denied`](../ERROR_BOOK.md#permission-denied) | Permiso denegado | No se cambió nada. |
| [`provider_unavailable`](../ERROR_BOOK.md#provider-unavailable) | Proveedor inaccesible | No se envió nada. |
| [`provider_exhausted`](../ERROR_BOOK.md#provider-exhausted) | Límite del proveedor alcanzado | La petición llegó al proveedor. |
| [`timeout`](../ERROR_BOOK.md#timeout) | Tiempo de espera agotado | Desconocido: comprueba antes de reintentar. |
| [`cancelled`](../ERROR_BOOK.md#cancelled) | Cancelado | Desconocido: puede que una parte ya se haya ejecutado. |
| [`credential_failure`](../ERROR_BOOK.md#credential-failure) | No se pudo leer una credencial guardada | No se envió ni se cambió nada. |
| [`unknown`](../ERROR_BOOK.md#unknown) | Error inesperado | Desconocido: comprueba antes de reintentar. |
| [`wallet_quote_mismatch`](../ERROR_BOOK.md#wallet-quote-mismatch) | La aprobación ya no coincide con la vista previa | No se firmó ni se envió nada. |
| [`wallet_broadcast_failed`](../ERROR_BOOK.md#wallet-broadcast-failed) | No se pudo difundir el pago | Desconocido: comprueba el estado del pago antes de reintentar. |
| [`wallet_duplicate_payment`](../ERROR_BOOK.md#wallet-duplicate-payment) | Pago ya propuesto o enviado | No se envió nada nuevo; el pago anterior consta en el registro. |

## Errores y otros resultados

VOOL los mantiene separados, y su forma de expresarlos también:

* **Un error.** Algo falló. El código y el mensaje dicen qué, y qué hacer.
* **Esperándote.** Hay abierta una solicitud de permiso o una aprobación de pago. No se ha ejecutado nada,
  y no se ejecutará nada hasta que respondas. Consulta [Permisos](../concepts/permissions.md).
* **Detenido por un límite de gasto o una protección de precio.** El rechazo ocurre antes de contactar con el
  proveedor, y el mensaje nunca da a entender que se consultó al proveedor. Consulta
  [Límites de gasto y protecciones de precio](../guides/spending-limits.md).
* **Cancelado.** Lo detuviste tú, o lo detuvo VOOL, antes de que terminara. Puede que una parte ya se haya
  ejecutado, así que comprueba qué cambió antes de volver a empezar.
* **No es posible aquí.** Esta versión o este equipo no puede hacerlo, y el mensaje indica qué
  puede hacerlo: una oferta de pago cuyo esquema VOOL no puede comprobar de principio a fin, o un extra opcional
  que no está instalado.
* **Resultado desconocido.** La acción puede haber terminado o no, como un pago cuya
  difusión nunca se confirmó. Comprueba primero su estado. VOOL nunca vuelve a pagar a ciegas.

## Errores con sus propios códigos

Algunas partes de VOOL explican sus fallos con sus propios códigos cortos, en la pantalla donde
ocurre el fallo, y no están en el Libro de errores: los adjuntos (por ejemplo, un archivo demasiado
grande o una credencial encontrada en un archivo pegado), las actualizaciones de la app, las carpetas de proyecto,
la selección de modelo, los límites de gasto, los contactos, los plugins y las habilidades, la entrada de voz, los
datos guardados y los informes de errores. Sus mensajes dicen qué pasó y qué hacer de la misma manera. Consulta
[Chats y adjuntos](../concepts/chats-and-attachments.md),
[Actualizaciones y migración](updates-migration.md) e
[Informes de errores y privacidad](../trust/bug-reporting.md).

## Un código que no reconoces

Un código que no está en el Libro de errores no procede de esta versión de VOOL. Comprueba qué
versión lo mostró y busca después en el [Libro de errores](../ERROR_BOOK.md) de esa versión. Si
procede de esta versión, infórmalo con el código exactamente como aparece, y con la referencia
(`fault-` y doce caracteres) cuando la respuesta incluía una. Un fallo que VOOL no puede
clasificar se muestra como [`unknown`](../ERROR_BOOK.md#unknown), nunca como una causa supuesta.
Consulta [Informes de errores y privacidad](../trust/bug-reporting.md).

## Solucionar síntomas concretos

Consulta [Solución de problemas](../help/troubleshooting.md) para problemas de instalación, de modelos y de
plataforma, y [Copia de seguridad y recuperación](../help/backup-and-recovery.md) para recuperar tus chats, tu
memoria y tu monedero.
