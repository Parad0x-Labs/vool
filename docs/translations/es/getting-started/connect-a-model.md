---
description: Cómo conectar un modelo de IA a VOOL: un modelo local que se ejecuta en tu propio ordenador, o un proveedor en la nube con tu propia clave API.
translation: machine
source_sha256: fdc85c0cf7cf8399666b8dd3d49ddbfb7cbe65ca7800be4630d88bd86053987a
---

# Conectar un modelo

VOOL no incluye ningún modelo. Tú eliges uno, y esa elección determina si una petición
se queda en tu equipo o sale de él.

## Local

Si hay un entorno de ejecución local instalado y en marcha, VOOL lo detecta y muestra sus modelos.
Al seleccionar uno, el indicador muestra Local. Los detalles de configuración están en
[Ejecutar un modelo local](../guides/local-models.md).

## Nube

Añade una clave de proveedor en **Ajustes → Claves API**. La clave se guarda en un archivo cifrado en este
ordenador (consulta [Modelo de seguridad](../trust/security.md#claves)) y se usa para enviar las peticiones
**desde tu equipo directamente al proveedor**.

Al seleccionar un modelo en la nube, el indicador muestra el nombre del proveedor, y cambia antes de
que se haga ninguna petición.

Consulta [Conectar OpenRouter](../guides/openrouter.md) para el proceso específico de OpenRouter.

## Cambiar de modelo

Cambiar de modelo cambia de modo. El indicador es la referencia: si muestra Local,
la siguiente petición se procesa en este ordenador.
