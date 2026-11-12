---
description: Un espacio de trabajo es la carpeta del proyecto en la que trabaja VOOL. Fuera de ella, VOOL puede leer las carpetas visibles de tu carpeta personal y cambiar archivos solo en Escritorio, Descargas y Documentos.
translation: machine
source_sha256: 33b648a0a55f99b1c96365553942911862672eafa55e94bd45f18ce566d5fcea
---

# Espacios de trabajo

Un espacio de trabajo es la carpeta del proyecto en la que trabaja un chat. VOOL lee y cambia archivos ahí,
y antes pregunta siempre que tu modo de funcionamiento así lo indique (consulta [Herramientas](tools.md)).

## Añadir y quitar

Selecciona una carpeta con **Elige una carpeta de proyecto…** en el chat, o con **+ Proyecto** en la
barra lateral del chat. Al eliminar un proyecto, VOOL olvida lo que aprendió en él y devuelve sus chats
a General. Tus archivos no se tocan.

## Tus otras carpetas

Fuera del espacio de trabajo, las herramientas de archivos de VOOL también pueden acceder a tu carpeta personal, con límites fijos:

| Carpetas | Qué puede hacer VOOL |
| --- | --- |
| Escritorio, Descargas, Documentos | Leer y cambiar archivos, preguntando antes según indique tu modo de funcionamiento |
| Imágenes, Películas (Vídeos en Linux), Música | Solo lectura: listar, buscar, abrir y medir; nunca cambiar |
| Las demás carpetas visibles de tu carpeta personal, como `~/Projects` | Solo lectura |
| Carpetas ocultas (como `~/.ssh`), datos de apps y del sistema (`Library`, `Applications`, `AppData`), la carpeta de datos de VOOL | Nunca |

VOOL solo mira en estas carpetas cuando una petición lo necesita, por ejemplo «busca mi PDF de la renta» o
«qué vídeo de Películas es el más grande». No indexa tu disco en segundo plano.

## Consejos para acotar el alcance

* Apunta el espacio de trabajo a un proyecto, no a tu carpeta personal.
* Guarda los archivos de credenciales, el material de claves y los almacenes de contraseñas en un gestor de contraseñas o en una
  carpeta oculta, no sueltos en Escritorio, Descargas, Documentos ni en una carpeta de proyecto.
* Usa un proyecto distinto para cada trabajo, para que el contexto de uno no llegue a otro.

{% hint style="warning" %}
Todo lo que VOOL lee, en la carpeta del proyecto o en las carpetas de tu carpeta personal, puede incluirse en lo que
se envía a tu proveedor cuando hay un modelo en la nube seleccionado. Acota el alcance con cuidado.
{% endhint %}
