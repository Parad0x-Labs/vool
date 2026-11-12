---
description: Cómo ejecutar modelos de IA en local en tu propio ordenador con VOOL: instalar un servidor de modelos local, elegir el tamaño del modelo y trabajar totalmente sin conexión.
translation: machine
source_sha256: d93b9995b0f4fec8aca12bb90d8d6f9ff0fd9623c43d4529913dc76f055ac24c
---

# Ejecutar un modelo local

## Instalar un entorno de ejecución

VOOL se comunica con un servidor de modelos local. La app para macOS incluye su propio ejecutor de modelos locales,
así que en un Mac puedes saltarte este paso. En Linux y en las instalaciones desde el código fuente, instala un
servidor como Ollama, inícialo y descarga un modelo.

```bash
ollama serve
ollama pull qwen2.5-coder:7b
```

## Apuntar VOOL hacia él

Abre **Ajustes → Modelos y proveedores**. Los entornos de ejecución locales que estén en marcha se detectan
automáticamente. Selecciona un modelo y el indicador de modo cambia a Local.

## Certifícalo una vez

Un modelo local solo escribe respuestas después de certificarse. Abre **Ajustes → Modelos y proveedores → Modelos locales**,
registra el modelo y después elige **Certify**. La certificación ejecuta una breve comprobación de herramientas en tu
equipo; no se descarga nada y no se contacta con nadie. Hasta que un modelo la supera, VOOL no
le deja escribir respuestas finales.

## Tamaño

| Tamaño del modelo | Memoria RAM necesaria | Adecuado para |
| --- | ---: | --- |
| 3B | ~4 GB | Ediciones cortas, preguntas rápidas |
| 7–8B | ~8 GB | Uso general, la mayor parte de la programación |
| 14B | ~16 GB | Razonamientos más largos |
| 32B+ | 32 GB o más | Trabajo más pesado, más lento |

{% hint style="info" %}
Las versiones cuantizadas reducen mucho la memoria RAM necesaria. Un modelo de 7B a 4 bits funciona con holgura en
unos 5 GB.
{% endhint %}

## Si va lento

* Elige un modelo más pequeño o más cuantizado.
* Cierra otras aplicaciones que usen mucha memoria RAM.
* Acorta el contexto: menos archivos en la petición significa menos que procesar.
