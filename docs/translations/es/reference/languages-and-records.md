---
description: Idiomas, accesibilidad, historial de actividad, recibos y cómo se verifican las respuestas de los modelos.
translation: machine
source_sha256: 2e135f9673ae465cee9db53572ab89c93235805dfea713efd73d7f4244b6b1f5
---

# Idiomas, accesibilidad y registros

## Idiomas de la interfaz

La interfaz de VOOL está disponible en varios idiomas. La elección del idioma solo afecta a la
presentación: las decisiones de permisos, la autoridad de gasto y los códigos de error son
**independientes del idioma por construcción**; un mensaje de error traducido nunca puede ampliar ni
restringir lo que decidió la autoridad en inglés.

## Accesibilidad

* Atajos de teclado para los flujos principales (consulta [Atajos de teclado](shortcuts.md)).
* Presentación clara y oscura en la documentación y en las pantallas de la app, que sigue la preferencia de tu
  sistema y se puede cambiar a mano.
* Los errores se indican con texto, no solo con color; las acciones destructivas siempre requieren una
  confirmación explícita, en lugar de un solo clic por error.

## Actividad

La vista de actividad es el historial completo de lo que hizo VOOL: las llamadas a herramientas con sus entradas y
resultados, las aprobaciones que concediste o rechazaste, y los pagos con sus recibos. Un turno que
detuvo una protección muestra la detención y la protección: un rechazo es un evento registrado, no una
ausencia.

## Recibos

El dinero y las acciones que afectan al gasto escriben **recibos**: qué se propuso, qué se aprobó,
qué se envió y qué se liquidó. Los resultados desconocidos se registran como desconocidos; nunca se cuentan
como éxito ni como fracaso. Esto es lo que hace que *«comprueba su estado antes de reintentar»* se pueda
seguir: el recibo te dice en qué estado está realmente el pago.

## Estado de verificación del modelo

VOOL distingue lo que un modelo **dijo** de lo que el entorno de ejecución **demostró**:

* Los resultados de las herramientas se registran con sus registros de ejecución; las afirmaciones de una respuesta
  sobre archivos, pagos o datos se vinculan a esos registros, y las afirmaciones sin respaldo se retienen.
* Los recibos llevan un estado de verificación; cuando una vía es experimental o no verificable en esta
  versión, el estado lo dice en lugar de dar a entender una prueba.

Consulta [Recibos](../concepts/receipts.md) para el concepto y [Errores](errors.md) para ver cómo
se clasifican los fallos.
