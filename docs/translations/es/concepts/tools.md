---
description: Las herramientas son las acciones que VOOL puede realizar, y cada una queda registrada.
translation: machine
source_sha256: 53722a7f30f4446b6d029465a402a823f60496a66008b5df150d201c1963b152
---

# Herramientas

Una herramienta es una acción que el entorno de ejecución puede realizar en tu nombre: leer un archivo, ejecutar un
comando, buscar en una carpeta.

## Inspeccionables por diseño

Cada llamada a una herramienta registra qué se pidió, qué se ejecutó y qué se obtuvo. Una ejecución se puede
revisar después sin volver a ejecutarla.

## Permiso

Que una herramienta se ejecute de inmediato o pregunte antes depende del modo que elegiste y de la acción.
Un chat nuevo empieza en Manual, donde cada cambio pregunta antes. En Auto, crear y editar
archivos, los comandos y las acciones de red se ejecutan sin preguntar, mientras que eliminar, sobrescribir,
instalar, los commits y push de Git, el despliegue, enviar mensajes, los pagos y cambiar los
ajustes siguen preguntando. En todos los modos, las herramientas de archivos solo funcionan dentro del
[espacio de trabajo](workspaces.md), las herramientas de búsqueda y de descarga leen recursos públicos sin
escribir nada, y los secretos guardados nunca se leen. [Permisos](permissions.md) explica los cinco modos y qué
permite cada botón de aprobación.

```text
tool: read_file
path: src/main.py
result: 412 lines
```

Consulta también [Recibos](receipts.md).
