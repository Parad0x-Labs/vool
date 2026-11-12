---
description: Soluciones a los problemas más habituales.
translation: machine
source_sha256: 809bae11bc4bf8c2d707cfef616f80056a5d98d35b0786cc937a40526e802689
---

# Solución de problemas

## macOS se niega a abrir la app

Es lo esperado: la app aún no está notarizada. Comprueba primero la suma de verificación y después abre
**Ajustes del Sistema → Privacidad y seguridad** y haz clic en **Abrir igualmente**. En macOS 14 también puedes
hacer clic en VOOL con la tecla Control pulsada y elegir **Abrir**.

## Windows

No hay descarga para Windows en esta versión. En Windows, VOOL se instala desde el código fuente y es
experimental; consulta la [guía de instalación](../getting-started/install.md#instalar-desde-el-código-fuente).

## No aparece ningún modelo local

1. Comprueba que el entorno de ejecución está en marcha.
2. Comprueba que hay al menos un modelo descargado.
3. Comprueba que el puerto coincide con el que espera VOOL.

```bash
curl http://localhost:11434/api/tags
```

Si eso no devuelve nada, el entorno de ejecución no está en marcha.

## Las respuestas son lentas

Normalmente se debe al tamaño del modelo frente a la memoria RAM disponible. Consulta
[Ejecutar un modelo local](../guides/local-models.md).

## Un error muestra un código

Cada fallo tiene un código fijo. [Errores y códigos de error](../reference/errors.md) explica cómo
leerlo, y el [Libro de errores](../ERROR_BOOK.md) indica para cada código qué pasó, qué puede haber
pasado ya y qué hacer a continuación.

## Empezar de cero

Cierra VOOL y elimina la carpeta «runtime home» indicada en
[Configuración](../reference/configuration.md). Esto borra tus chats, tu memoria, tus ajustes, tus
claves guardadas y cualquier monedero que VOOL guarde en este ordenador, así que guarda primero lo
que necesites: consulta [Copia de seguridad y recuperación](backup-and-recovery.md). Los archivos
del espacio de trabajo no se tocan.
