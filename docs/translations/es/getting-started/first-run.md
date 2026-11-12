---
description: Qué te pregunta el asistente de IA VOOL la primera vez que lo abres, y qué no hace.
translation: machine
source_sha256: cedf20410d47a2d277e5522bb3e55169854a91a267d71193a762e072e7f8e47e
---

# Primer inicio

El primer inicio hace cuatro preguntas breves. Puedes saltarte cualquiera y cambiar todas las respuestas
más adelante. No se envía nada a ningún sitio mientras respondes, salvo la comprobación de una clave de la nube
con su proveedor si añades una.

## Qué ocurre

1. **¿Dónde debe pensar?** En este ordenador con un modelo local, o en línea con una clave de un
   proveedor al que pagas. Consulta [Conectar un modelo](connect-a-model.md).
2. **Elige una carpeta.** La carpeta del proyecto en la que trabaja VOOL. Consulta
   [Espacios de trabajo](../../../concepts/workspaces.md).
3. **¿Qué puede hacer solo?** Con qué frecuencia VOOL se detiene y te pregunta antes de cambiar algo. Consulta
   [Herramientas](../../../concepts/tools.md).
4. **¿Cómo debe llamarte?** Opcional. Basta con un nombre o un apodo, y se queda en
   este ordenador.

A continuación, el indicador de modo muestra Local, Auto o el nombre del proveedor en la nube, y refleja el
modelo seleccionado en ese momento.

**Certifica un modelo local una vez.** Antes de que un modelo local pueda responder, abre
**Ajustes → Modelos y proveedores → Modelos locales** y pulsa **Certify** para ese modelo. Esto ejecuta una
breve comprobación en tu equipo.

## Qué no hace VOOL en el primer inicio

* No crea una cuenta.
* No exige una clave de la nube. Un modelo local basta para usar la app.
* No indexa tu disco. Solo lee archivos cuando una petición los necesita; consulta
  [Espacios de trabajo](../../../concepts/workspaces.md) para ver a qué carpetas puede acceder.

{% hint style="info" %}
Puedes elegir otra carpeta de proyecto en cualquier momento con **+ Proyecto** en la barra lateral del chat.
{% endhint %}

## Siguiente

Continúa con [Conectar un modelo](connect-a-model.md).
