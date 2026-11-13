---
description: Cómo usar VOOL como asistente de IA para programar en tu propio código: configurar el espacio de trabajo, preguntar sobre el código y revisar las ediciones.
translation: machine
source_sha256: 1d1c4e7c4222eed4dc6caae24dca70729496ead8b88b1fc45a81a61ed548241a
---

# Programar con VOOL

## Usa el repositorio como espacio de trabajo

Apunta el [espacio de trabajo](../concepts/workspaces.md) a la raíz del proyecto. VOOL lee los
archivos que necesita en lugar de cargar todo el árbol.

## Pregunta en términos del código

Nombrar un archivo o un símbolo le da al entorno de ejecución un punto de partida:

> Busca dónde se configura el tiempo de espera de la sesión en `src/auth/` y explica qué pasa cuando
> caduca.

## Revisa antes de aplicar

En el modo Manual, cada edición pregunta antes. En el modo Auto, las ediciones y los comandos habituales pueden
ejecutarse automáticamente y aparecen en el registro de actividad, mientras que las eliminaciones y las sobrescrituras
siguen preguntando. Haz un commit antes de un cambio grande para que el diff sea fácil de leer y fácil de deshacer.

## Local o nube

Programar funciona en ambos modos. Un modelo local de programación de 7B se encarga de la mayoría de las ediciones del
día a día; los modelos en la nube más grandes se encargan de los razonamientos más largos. El indicador de modelo te
dice cuál está activo antes de enviar: `LOCAL` para un modelo local, `CLOUD` para uno en la nube y, de forma
predeterminada, `AUTO` (`VOOL Auto`, `Local first`) cuando VOOL elige.
