---
description: Qué datos envía VOOL exactamente, cuándo y a quién. Qué se queda en tu ordenador y qué llega a tu proveedor de IA.
translation: machine
source_sha256: 4fd0aaa4d2480ae80b94aa7132c46c8263113450e4b02c5747388791e7bc8710
---

# Tratamiento de datos

## Los dos estados

**Local**: el modelo y tus archivos se quedan en este ordenador. Los datos en directo como
el tiempo o los precios vienen de servicios web públicos solo mientras Búsquedas web está activado (está desactivado en una instalación nueva), y nunca en Solo local.

**Nube**: el contexto seleccionado se envía directamente al proveedor seleccionado.

La cabecera del chat muestra de qué forma se ejecuta la siguiente petición; por ejemplo, **Auto** con
**Local primero** cuando VOOL Auto responde desde este ordenador.

## Qué no hace VOOL

* No pasa tus prompts por la infraestructura de Parad0x Labs.
* No sube tu espacio de trabajo.
* No envía tu clave a ningún sitio salvo al proveedor al que pertenece.

## OpenRouter en concreto

Tu cuenta de OpenRouter sigue siendo tuya. VOOL envía las peticiones directamente desde este ordenador
con tu clave. Parad0x no hace de intermediario de tus prompts ni paga tu uso. El uso real de VOOL
puede aparecer en las estadísticas públicas y agregadas de VOOL en OpenRouter, pero los prompts individuales
y las identidades no se publican.

## Telemetría

VOOL no envía telemetría del producto. Un informe de error solo sale de tu equipo cuando lo previsualizas
y decides enviarlo; consulta [Informes de errores y privacidad](bug-reporting.md).

## Se aplican las condiciones de tu proveedor

En modo nube, tus datos llegan a tu proveedor bajo **sus** condiciones de conservación y
entrenamiento, no las de VOOL. Lee las condiciones del proveedor que elijas.
