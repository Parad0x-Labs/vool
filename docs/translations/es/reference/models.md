---
description: Qué modelos de IA funcionan con VOOL: modelos locales en tu propio hardware y modelos en la nube a través de tu cuenta del proveedor.
translation: machine
source_sha256: 2ce84ebed0545059de772cb175bd8f088ee2d22feb60d4256ee7800f20097824
---

# Modelos compatibles

VOOL no incluye ningún modelo: el primer modelo local se descarga la primera vez que lo
usas. Se conecta a los entornos de ejecución y a los proveedores que tú eliges.

## Entornos de ejecución locales

Ollama, llama.cpp, vLLM y MLX se detectan automáticamente. Otros entornos de ejecución que expongan un
endpoint compatible con OpenAI en localhost se pueden indicar a mano.

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
