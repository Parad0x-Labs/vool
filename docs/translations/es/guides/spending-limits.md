---
description: Límites de gasto, protecciones de precio y qué ocurre cuando se interrumpe una llamada de pago.
translation: machine
source_sha256: a4a5246059942702330cbfc18e9872dc5b0f46192fa5b852bfc9060ca987eae6
---

# Límites de gasto y protecciones de precio

VOOL nunca gasta dinero en tu nombre sin un tope explícito que tú controlas. Esta página
describe las capas que protegen las llamadas a modelos de pago y los recursos de pago, y qué ocurre cuando una
llamada de pago se interrumpe a medias.

## Dos precios, una regla

* **Precio de catálogo**: lo que un proveedor anuncia actualmente para un modelo. VOOL lo descubre; es
  información, no un permiso.
* **Tu precio aceptado**: el tope que aprobaste para ese proveedor y ese modelo en Ajustes.
  Un precio descubierto por encima de tu tope se rechaza **antes** de contactar con el proveedor. El
  rechazo dice exactamente eso: el modelo no se ejecutó, no se envió nada y no se cobró nada.

## Límites de gasto

Para las llamadas de pago, VOOL reserva dinero contra los límites antes de cada llamada:

| Límite | Significado |
| --- | --- |
| Por llamada | El tope del coste previsto de una sola llamada. |
| Por tarea | Lo máximo que puede gastar una tarea en todas sus llamadas. |
| Diario / mensual | Topes del uso de pago para el periodo completo. |

Una llamada que superaría un límite se rechaza antes de enviarse. El rechazo nombra el límite que
la rechazó (`per_task_spend_cap_exceeded` y los códigos similares) y la redacción es deliberadamente
previa al envío: *«running it would exceed your spend cap»*, nunca una afirmación de que se contactó con
el proveedor.

## Un modelo fijado nunca se sustituye en silencio

Si eliges explícitamente un modelo de pago y una protección lo rechaza, VOOL **no** responde sin decir nada
con un modelo más barato. El turno falla nombrando el modelo que elegiste y la barrera real, con la
frase *«no other model was substituted.»* Elige tú otro modelo o sube el límite de forma
deliberada.

## Pagado, pero la respuesta nunca llegó

Si una llamada de pago se cobró pero su respuesta no llegó, el resultado es **desconocido**: VOOL
no fingirá que falló ni que tuvo éxito:

* No se inventa ninguna respuesta en su lugar, y no se da por hecho ningún reembolso.
* El pago queda registrado como no resuelto.
* Ajustes lo muestra en **Paid calls waiting for an answer**, donde **Resume** pide la
  misma respuesta una vez, **sin volver a pagar**.

Por eso nunca se sugiere reintentar a ciegas en estos casos: un simple reenvío podría pagar
dos veces. Resume es la única vía admitida.

## Pagos automáticos desde el monedero (x402)

Los sitios web que piden un pago x402 en USDC en Base o Base Sepolia pueden cobrarse desde tu
monedero Crypto Pilot, con una aprobación con PIN dentro de estos límites; todavía no se ha hecho ningún pago real
en mainnet con esto. La opción de pago por llamada del mercado de modelos de Ajustes paga desde el
monedero de Solana Mainnet, con una aprobación por llamada dentro de estos límites, y aún no se ha
probado en una cadena real.
Los recursos que se pagan con el monedero (consulta [Monedero](../guides/wallet.md)) tienen su propio límite de pago
automático. Un recurso que pide más que ese límite simplemente no se paga; puedes subir el límite de forma
deliberada o pagar el recurso mediante una propuesta explícita con tu aprobación.
