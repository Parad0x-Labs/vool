---
description: El monedero opcional Crypto Pilot: redes, pagos x402, comisiones DNA y recuperación, tal como los incluye realmente esta versión.
translation: machine
source_sha256: 46121a5ed26be175ddd3c66b269df356e074adbb57c8e9806461b80197613cc2
---

# Monedero

El monedero es **opcional y está desactivado de forma predeterminada**. Existe para que VOOL pueda guardar una pequeña
cantidad de criptomonedas para servicios de IA que cobran por petición. No necesitas nada de él para usar VOOL.

Esta página describe lo que la versión actual admite realmente. El monedero está en fase **piloto**:
las capacidades se añaden vía por vía, y cada vía de las que siguen indica exactamente qué hace.

## Entornos de red

Cuando Cripto está activado, VOOL funciona en **Mainnet**: Solana, Robinhood Chain, Base, Ethereum, BNB y
Arbitrum One.
La configuración normal nunca te pide que elijas un entorno de red.

* **Mainnet**: el entorno predeterminado de toda instalación, incluidas las instalaciones actualizadas que tienen
  cuentas antiguas de redes de prueba. Todos los límites de gasto, aprobaciones y recibos se aplican como se
  describe más abajo.
* **Test networks**: Solana Devnet, Robinhood Chain Testnet y las demás vías de prueba. Solo para
  desarrolladores: cámbialo en **Ajustes → Cripto → Developer options**.

Las cuentas, los saldos y los recibos pertenecen a un entorno y nunca pasan de uno a otro; las cuentas
del otro entorno siguen apareciendo en su propia red. Una petición para una red del otro
entorno se rechaza sin crear, proponer ni firmar nada.

{% hint style="warning" %}
La documentación anterior decía que mainnet era imposible en esta versión. Eso era cierto antes del
piloto y ya no lo es: ahora existen filas de mainnet. Lo que sigue siendo cierto es más limitado: consulta las
secciones de transferencias y pagos de más abajo para ver exactamente qué vías existen en mainnet.
{% endhint %}

## Qué se mueve por cada vía

* **Las transferencias mueven monedas nativas** (por ejemplo, SOL en Solana, ETH en Base, Ethereum o
  Arbitrum One) y **USDT en Solana, Ethereum, BNB Smart Chain y Arbitrum One**.
  La cadena forma parte de cada dirección de contacto guardada y de cada aprobación. Las transferencias de tokens
  también necesitan la moneda nativa de esa cadena para las comisiones; la cotización muestra ambos importes dentro
  de tus límites.
* **USDC en Solana y Base solo está disponible para los canales de pago.** No se ofrece
  para los envíos normales. La vista principal de saldo del monedero muestra las monedas nativas, no el conjunto
  completo de tus tokens.
* Cada transferencia se previsualiza (una **cotización**: importe, comisión máxima, destinatario, red) y necesita
  tu aprobación explícita vinculada exactamente a esa vista previa. Si cambias algo, la aprobación queda
  anulada y no se firma nada.
* Un nombre que coincide con varios contactos o direcciones de monedero te pide que elijas. Revisa la
  dirección y la cadena exactas. Una dirección cambiada o un contacto renombrado invalida la revisión anterior.
* En mainnet, solo envía un VOOL Wallet creado en este dispositivo. Un monedero externo que hayas conectado
  o una dirección de solo lectura pueden recibir y mostrar su saldo; un envío o un pago por un canal de pago
  desde ellos se rechaza antes de pedir ninguna aprobación, y no se firma nada.
* Actividad registra los pagos hechos a través de VOOL y sus recibos. No es un historial completo de los
  pagos entrantes. Este monedero no ofrece ninguna acción de swap ni de bridge.

## Cómo se aprueba un pago

1. VOOL prepara una propuesta con los importes exactos y una comisión máxima.
2. Tú la apruebas con tu PIN o contraseña (o con la presencia en el dispositivo, donde esté disponible).
3. El pago se firma y se envía, y se registra un recibo.

Las aprobaciones no pasan de una propuesta a otra. Un PIN incorrecto no firma nada. Demasiados intentos
fallidos bloquean el monedero durante un tiempo, de forma deliberada y con una espera indicada.

## Pagos automáticos x402

x402 es un protocolo de pago por petición que usan algunos sitios web y servicios de IA. **Los sitios web
que piden un pago x402 en USDC en Base** (o en Base Sepolia, en Developer options) pueden cobrarse
desde tu monedero Crypto Pilot: una aprobación con tu PIN, dentro de tus límites. El servicio de pago del
sitio web cubre la comisión de red. **Todavía no se ha hecho ningún pago real en mainnet con esto.** Las demás
redes de prueba (Solana Devnet, Ethereum Sepolia y BNB testnet) siguen en Developer options.
**Ajustes → Cripto → Payment channels** muestra todas las formas en que el monedero puede pagar a un sitio web,
cada una con su propio interruptor; un canal desactivado rechaza el pago antes de firmar nada. Pagar mediante
Solana pay-kit no está incluido en las descargas, así que ese canal aparece como no utilizable. **Mercado de
modelos:** la opción de pago por llamada del mercado de modelos de Ajustes puede pagar desde el monedero Crypto Pilot
de Solana Mainnet en USDC o SOL. Cada llamada espera tu aprobación dentro de los límites de tu monedero y muestra
la comisión de VOOL del 0,1 %. Esa vía aún no se ha probado en una cadena real.
Cuando un recurso de pago usa x402:

* Los pagos pasan por los mismos límites y el mismo flujo de aprobación del monedero.
* Los pagos automáticos tienen un límite por pago; un recurso por encima del límite **no se paga** (puedes
  subir el límite de forma deliberada o pagarlo mediante una propuesta explícita).
* Si un pago no pudo completarse, el resultado se indica como **desconocido**: comprueba su estado
  antes de reintentar; VOOL no te invitará a hacer un pago duplicado a ciegas.

## Comisión de servicio DNA

La vía x402 de pago por llamada del mercado de modelos cobra una comisión de servicio del **0,1 % (10 puntos
básicos)** sobre cada pago aceptado a un proveedor, que se muestra aparte del pago y aparte de la comisión de red.
Nada más la genera: los envíos normales, la creación de monederos, la inferencia local y las llamadas con tu propia
clave API (BYOK) **no** llevan comisión de servicio. Las comisiones acumuladas se cobran de forma económica (con un
pago nativo posterior, con una sola aprobación, solo cuando el coste del cobro está acotado); nunca mediante un cargo
aparte por sorpresa.

## Recuperación

* La **frase de recuperación se muestra exactamente una vez**, al crear el monedero.
* La clave de respaldo de un monedero piloto también se entrega una sola vez durante la configuración, y no hay
  ninguna exportación posterior. Si no la guardaste, cancela la configuración y no envíes fondos a esa dirección.
* Un VOOL Wallet antiguo, creado antes de Crypto Pilot, puede exportar su clave privada una vez para importarla en
  otra app de monedero. La exportación solo se abre con el inicio de sesión de tu propio Mac (Touch ID o la
  contraseña de tu Mac), nunca con el PIN del monedero, y la clave se muestra una vez sin guardarse.
* ¿Has perdido un monedero? Recupéralo con la frase que anotaste al crearlo. Una prueba de recuperación que
  no corresponde exactamente a este monedero se rechaza antes de escribir nada.
