---
description: Contactos: personas y destinos guardados, con una revisión de cambios que protege cada edición.
translation: machine
source_sha256: 8be5d771229a4d6c4b439af014f0407649ed694fd26f9fd880009d25f5b8a347
---

# Contactos

La lista de contactos es una libreta de direcciones local y estructurada (personas, sus destinos y sus alias
de pago) que VOOL puede usar en lugar de pedirte que vuelvas a escribir una dirección. Como una libreta de
direcciones alimenta pagos y mensajes, sus ediciones están **protegidas**: se revisan, se confirman y
se registran.

## Ediciones protegidas

Cambiar un contacto guardado es un proceso de dos pasos:

1. **Revisar**: el cambio se prepara y se muestra. Si la entrada cambió desde que la abriste
   (otra ventana, otro dispositivo), el cambio se rechaza con *«changed after this was
   reviewed; review it again. Nothing changed.»*
2. **Confirmar**: el cambio solo se guarda con tu PIN o tu contraseña en la pantalla de revisión.

Cada rechazo indica claramente que **no se guardó ni se cambió nada**, porque así fue. Si
tu PIN se aceptó pero el guardado en sí falló, el mensaje dice exactamente eso: el PIN se aceptó;
vuelve a guardar antes de que caduque la confirmación.

## Protecciones

* Un solo cambio por contacto a la vez; un número limitado de cambios pendientes.
* Eliminar un contacto no reescribe el historial en silencio: las referencias a un contacto eliminado se
  indican como *«That contact was deleted»*; nunca se vuelven a resolver en silencio.
* Las importaciones nunca se aplican a medias: si no se pudo leer el origen, no se importó nada.

## Por qué este diseño

Una dirección de pago con una errata es dinero irrecuperable. El paso de revisión existe para que ningún turno
del modelo, ninguna edición masiva y ninguna ventana desactualizada puedan cambiar a quién corresponde «Alice» sin
que una persona confirme exactamente la entrada resultante.
