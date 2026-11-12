---
description: Soluciones a los problemas más habituales.
translation: machine
source_sha256: 792c90fd68caf7409212d3742109c46e49d1f7e640aa3e9f70db9fe61ca55d77
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
[Ejecutar un modelo local](../../../guides/local-models.md).

## Empezar de cero

Cierra VOOL y elimina el directorio de configuración indicado en
[Configuración](../../../reference/configuration.md). Los archivos del espacio de trabajo no se tocan.
