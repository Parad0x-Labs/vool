---
description: Qué datos salen de tu ordenador cuando usas VOOL, y cuándo. La diferencia entre ejecutar la IA en local y usar un modelo en la nube.
translation: machine
source_sha256: 2c2b9e26e99579c00a032dfeac95fd216fe9229497c965c1bc08aface7a6c071
---

# Local y nube

VOOL puede responder de dos formas: en este ordenador o a través de un proveedor en la nube. La cabecera
del chat muestra de qué forma se ejecuta la siguiente petición; por ejemplo, **Auto** con
**Local primero** cuando VOOL Auto responde desde este ordenador.

## Local

El modelo y tus archivos se quedan en este ordenador. El modelo se ejecuta en un entorno de ejecución local, los archivos se leen
del disco y no se hace ninguna petición a ningún proveedor. Los datos en directo como el tiempo
o los precios vienen de servicios web públicos solo mientras Búsquedas web está activado (está desactivado en una instalación nueva), y nunca en Solo local.

## Nube (nombre del proveedor)

El contexto seleccionado se envía directamente al proveedor que hayas seleccionado, desde tu equipo
y con tu clave.

«El contexto seleccionado» es lo que la app ha reunido para esa petición: tu prompt, más
los archivos y los turnos anteriores que la petición necesita.

{% hint style="danger" %}
El modo nube envía datos fuera de tu equipo. Para eso sirve el modo nube. Lo que VOOL afirma se refiere
a la *ruta* (la petición va de tu ordenador a tu proveedor), no a que
no se transmita nada.
{% endhint %}

## Qué hay en medio

Nada operado por Parad0x Labs. No hay ningún proxy, pasarela ni relé para el tráfico de los modelos.
La app usa tu clave en tu equipo para hablar directamente con el proveedor.

Todos los detalles: [Tratamiento de datos](../trust/data-handling.md).
