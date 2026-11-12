---
description: Cómo recuerda VOOL las cosas de una sesión a otra y dónde se guarda esa memoria.
translation: machine
source_sha256: 65af63e755076fe33675d7a5d6afa7f0c5664b77f716ffc0dc1f0f358e67f667
---

# Memoria

La memoria es la forma en que VOOL conserva datos de una sesión a otra. Se guarda en archivos en tu
equipo, en la carpeta de datos de VOOL, en un formato legible.

## Qué tal recuerda

En 150 preguntas nunca usadas antes del benchmark de conversaciones largas LoCoMo, la memoria incluida en
VOOL 0.7 acertó 123 (82,0 %). En la comparación directa con las mismas preguntas, con el mismo
modelo respondiendo por los tres, VOOL obtuvo 124 (82,7 %), OpenClaw 107 (71,3 %) y Hermes 94 (62,7 %),
y envió entre 3,5 y 5,4 veces menos tokens por pregunta. Cada ejecución está publicada:
[benchmarks de memoria](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

## Propiedades

* **En disco y a la vista.** La memoria son archivos que puedes leer. `MEMORY.md` es la cara
  legible; los datos que hay detrás se guardan en un archivo de entradas junto a él.
* **Local.** La memoria no se sincroniza con ningún servidor.
* **Un almacén por carpeta de datos de VOOL.** La memoria no se divide por espacio de trabajo: se guarda
  para toda la instalación, así que no es una frontera de aislamiento por proyecto.

## Gestionarla

Las notas que añades tú mismo a `MEMORY.md` se conservan. Los datos que VOOL registró se borran
con la operación de olvidar de VOOL, no eliminando el archivo: olvidar reescribe el
almacén de entradas y la copia espejo en un solo paso y vuelve a leer ambos para confirmar que el texto
ha desaparecido. Eliminar solo `MEMORY.md` no es borrar: el archivo de entradas sigue conteniendo los
datos, y la copia espejo se reconstruye a partir de él.

En modo nube, la memoria relevante puede formar parte del contexto que se envía al proveedor, en
las mismas condiciones que cualquier otro contexto. Consulta [Local y nube](local-and-cloud.md).

Olvidar en un chat también borra las palabras del historial de ejecución de ese chat en la base de datos
del entorno de ejecución: los registros de turnos, los puntos de control, los intentos, los planes de tareas, las respuestas
finalizadas (con las copias que barre el borrado de respuestas), el conocimiento candidato, los informes de contexto,
las notificaciones, los resultados de investigación que produjo el chat y los mensajes fijados que los citaban. Los
registros de los que dependen otros registros se conservan con las palabras olvidadas en blanco, junto con cualquier
resumen criptográfico guardado a su lado; el resto se eliminan. Un turno que seguía en marcha cuando llegó la orden de
olvidar no puede volver a escribir las palabras: sus eventos se dejan en blanco y, cuando termina, sus registros
se borran de nuevo. Después se consolida el registro de escritura anticipada de la base de datos para que no queden
copias antiguas en el archivo `-wal`. Los registros de otros chats no se tocan. Un mensaje en cola que aún no se ha
enviado se conserva, y entonces la orden de olvidar indica que no se pudo aplicar por completo. Una tabla que ningún
paso de borrado conoce se nombra en la respuesta, que indica que reintentar no la limpiará.

Las palabras se comparan sin distinguir mayúsculas de minúsculas, y un dígito escrito en otro sistema de escritura
cuenta como ese dígito (un «１２» de ancho completo es 12). Un número olvidado solo coincide donde aparece
como número independiente: las letras y los dígitos escritos junto a él no pueden contener ningún otro
dígito, y no puede continuar a través de `.`, `,`, `:`, `/`, `-` o `_` hacia otro dígito. Olvidar «12» borra
«código 12», «12kg» y «B12», pero no «habitación 312», «3.12», «12.5», «12:30» ni «2026-10-12», y olvidar
«200» no borra «1,200». En el historial de ejecución, un número suelto que el entorno de ejecución contó o
cronometró (como un valor de `elapsed_ms` o `step_count`) no se tiene en cuenta.

Los lectores de memoria también comprueban una autoridad de olvido duradera. Una carpeta de memoria nueva la crea
antes de aceptar escrituras. Un almacén semántico antiguo se actualiza en su sitio solo cuando
no hay ningún borrado previo registrado; sus recuerdos conservados siguen disponibles. Una autoridad existente
que no se puede leer, una autoridad que falta con un testigo persistido o una barrera de olvido registrada
hacen que los lectores retengan la memoria afectada. Eliminar los archivos de la autoridad
no es una forma de recuperar contenido olvidado.

Un diario de borrado pendiente conserva su token para la recuperación. Una vez que existe la revocación
permanente y acotada, al completarse se elimina el token redundante del diario, pero se conservan
la identidad y el estado de la operación. La autoridad de revocación permanece.
