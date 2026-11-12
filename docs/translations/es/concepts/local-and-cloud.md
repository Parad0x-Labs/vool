---
description: Qué datos salen de tu ordenador cuando usas VOOL, y cuándo. La diferencia entre ejecutar la IA en local y usar un modelo en la nube.
translation: machine
source_sha256: 3217e13bf6d684853b92d19097627d07e64bdd17745560b1204871cb0b7b0753
---

# Local y nube

VOOL tiene dos estados. La interfaz siempre muestra cuál está activo.

## Local

Todo se queda en este ordenador. El modelo se ejecuta en un entorno de ejecución local, los archivos se leen
del disco y no se hace ninguna petición a ningún proveedor.

## Nube (nombre del proveedor)

El contexto seleccionado se envía directamente al proveedor que hayas seleccionado, desde tu equipo
y con tu clave.

«El contexto seleccionado» es lo que la app ha reunido para esa petición: tu prompt, más
los archivos y los turnos anteriores que la petición necesita. Se muestra antes de enviarlo.

{% hint style="danger" %}
El modo nube envía datos fuera de tu equipo. Para eso sirve el modo nube. Lo que VOOL afirma se refiere
a la *ruta* (la petición va de tu ordenador a tu proveedor), no a que
no se transmita nada.
{% endhint %}

## Qué hay en medio

Nada operado por Parad0x Labs. No hay ningún proxy, pasarela ni relé para el tráfico de los modelos.
La app usa tu clave en tu equipo para hablar directamente con el proveedor.

Todos los detalles: [Tratamiento de datos](../trust/data-handling.md).
