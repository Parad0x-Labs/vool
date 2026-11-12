---
description: Los chats, los chats privados y los archivos y textos largos que pegas en ellos: límites, qué se conserva y qué se puede rechazar.
translation: machine
source_sha256: 1497140fef69088f8e0210aeb8a7f6abba6ae82d5132841ce397fe60fa1bab99
---

# Chats y adjuntos

Un chat es una conversación con VOOL. Los chats se guardan en tu ordenador. Un chat puede vincularse a
una carpeta de proyecto, para que sus herramientas trabajen en esa carpeta (consulta [Espacios de trabajo](workspaces.md)), y
puedes llevar archivos a él como adjuntos.

## Adjuntar archivos

Usa **Adjuntar** junto al cuadro de mensaje o arrastra archivos sobre él. Cada archivo aparece como una ficha y
se envía con el siguiente mensaje que mandes. La línea bajo el cuadro muestra los límites:

* Hasta 8 archivos por mensaje, 10 MB por archivo y 25 MB por mensaje.
* Archivos de texto y de código, e imágenes PNG, JPEG, GIF y WebP. Los PDF, Word (DOCX), los archivos comprimidos
  como ZIP y los vídeos se suman cuando este ordenador tiene lo necesario para leerlos. El selector de archivos
  solo ofrece lo que este ordenador puede leer.

Lo que recibe el modelo también tiene límites: hasta 120.000 caracteres de un archivo y 240.000 del total de los
archivos de un mensaje. Un archivo más largo se corta al final de una línea, y el registro indica dónde. Si
el modelo que usas no puede leer imágenes, la ficha lo indica y la imagen no se le envía.

Antes de guardar una imagen, se eliminan sus datos ocultos: los datos de la cámara y de ubicación (EXIF,
XMP, IPTC) y el texto guardado dentro de los archivos PNG y WebP.

Un archivo que eliges se usa para ese único mensaje. Cuando la respuesta termina, el contenido del archivo se
borra y se conserva un recibo de él.

## Textos largos pegados

Si pegas 4.000 caracteres o más, VOOL lo guarda como un documento del chat en lugar de como un
mensaje, exactamente como lo pegaste. **Vista previa** muestra su comienzo. Un documento permanece en el chat
hasta que lo borras a él o borras el chat. Las preguntas posteriores en el mismo chat pueden usarlo: VOOL pasa al
modelo hasta 60.000 caracteres de los documentos anteriores del chat, empezando por los más recientes, e indica dónde
cortó.

## Cuando se rechaza un archivo

Un archivo rechazado nunca se descarta en silencio. Su ficha se queda e indica el motivo, y el mensaje
no se puede enviar hasta que quites el archivo o lo reintentes: *Quita o reintenta el adjunto que falló
antes de enviar.* Motivos habituales:

* **Demasiado grande, o demasiados archivos.** Quita algunos o envíalos en dos mensajes.
* **No es un tipo que VOOL lea, o no es lo que dice su nombre.** Un archivo llamado `.txt` que en realidad es una
  imagen o un programa, o un PDF para el que este ordenador no tiene lector, se rechaza con su motivo.
* **Contiene una credencial.** Un archivo o un texto pegado que parece llevar una clave o un token válidos, como
  un bloque de clave privada o un token de acceso, se rechaza y no se guarda nada. Quita el secreto
  y vuelve a adjuntar o a pegar.

Estos rechazos tienen sus propios códigos cortos, que se muestran con el mensaje. No están en el
[Libro de errores](../ERROR_BOOK.md), que cubre los demás errores de la app.

## Chats privados

Di «private chat», «don't record this» u «off the record», y el chat deja de guardar a partir de
ese mensaje: ni los mensajes, ni un resumen, ni la memoria. Un aviso muestra *Chat privado: no se guarda
nada*, y cada mensaje privado lleva un pequeño fantasma. Los mensajes privados no reaparecen
al recargar, y VOOL no puede recordarlos después. Di «turn recording back on» o
pulsa **Volver a activar el registro** para que el chat vuelva a guardarse desde el siguiente mensaje. Un chat privado
sigue siendo privado después de que VOOL se reinicie.

## Llevarte un chat

**Exportar** en la cabecera del chat guarda todo el chat como texto o Markdown, con todos los mensajes; los
archivos adjuntos se listan por su nombre, nunca con su contenido. Para mover un chat a otro ordenador
o guardar una copia firmada, consulta [Copia de seguridad y recuperación](../help/backup-and-recovery.md).
