---
description: Qué modelos de IA funcionan con VOOL: modelos locales en tu propio hardware y modelos en la nube a través de tu cuenta del proveedor.
translation: machine
source_sha256: 1528dfca8e391868d09156c42c1dd51514770ffab17077d9260d248842c82060
---

# Modelos compatibles

VOOL no incluye ningún modelo. Se conecta a los entornos de ejecución y a los proveedores que tú eliges.

## Entornos de ejecución locales

Se puede usar cualquier entorno de ejecución que exponga un endpoint compatible con OpenAI en localhost. La
detección es automática para los más habituales.

## Proveedores en la nube

A los modelos en la nube se accede con tu clave, desde tu equipo. Seleccionar uno pone la app en
modo nube (el indicador muestra el nombre del proveedor); consulta [Local y nube](../concepts/local-and-cloud.md).

[OpenRouter](../guides/openrouter.md) es la conexión individual más amplia: una clave, muchos
proveedores.

## Elegir

| Trabajo | Elección razonable |
| --- | --- |
| Archivos privados, sin red | Local, 7B o más |
| Programación del día a día | Modelo local de programación de 7–14B |
| Razonamientos largos | Un modelo en la nube más grande |
| Control de costes | Primero local, la nube cuando merezca la pena |
