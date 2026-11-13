---
description: Cómo ejecutar modelos de IA en local en tu propio ordenador con VOOL: instalar un servidor de modelos local, elegir el tamaño del modelo y trabajar totalmente sin conexión.
translation: machine
source_sha256: a19d4790729d13ed5e1b134bcb67ed3e7a80609561be4227ab9480492100c8a6
---

# Ejecutar un modelo local

## Instalar un entorno de ejecución

VOOL usa Ollama de forma predeterminada para ejecutar modelos locales; otros entornos de ejecución locales con un
endpoint compatible con OpenAI también funcionan. Ollama es un programa aparte, que se instala por separado
desde [ollama.com](https://ollama.com). La app para macOS incluye su propio ejecutor de modelos locales,
así que en un Mac puedes saltarte este paso. En Linux y en las instalaciones desde el código fuente, instala
Ollama, inícialo y descarga un modelo.

```bash
ollama serve
ollama pull qwen3:4b
```

VOOL elige el modelo local por ti de forma predeterminada: un modelo Qwen adecuado a tu ordenador. En
muchos ordenadores ese modelo es `qwen3:4b`; en ordenadores con 32 GB de memoria o más, el
predeterminado es un modelo Qwen más grande.

## Apuntar VOOL hacia él

Abre **Ajustes → Modelos y proveedores**. Los entornos de ejecución locales que estén en marcha se detectan
automáticamente. Selecciona un modelo y el indicador de modo cambia a Local.

## Déjalo listo una vez

Un modelo local solo escribe respuestas después de que VOOL lo haya comprobado. Abre
**Ajustes → Modelos y proveedores → Modelos locales** y pulsa **Usar este modelo** junto al modelo.
VOOL comprueba discretamente, en tu ordenador, que el modelo sabe usar las herramientas de VOOL lo bastante bien
como para responder por ti. La comprobación puede tardar un minuto o dos; no se descarga nada y no se contacta con
nadie. Cuando la supera, el modelo muestra **Listo** y VOOL responde con él. Hasta entonces, VOOL no le deja
escribir respuestas finales.

* **No encaja bien:** el modelo no sabe usar las herramientas de VOOL lo bastante bien. VOOL te muestra qué puedes hacer
  en su lugar: usar o descargar el modelo que ha elegido para este ordenador, o usar un modelo en la nube.
* **La comprobación no terminó:** asegúrate de que Ollama está abierto y pulsa **Intentar de nuevo**. Un ordenador lento
  puede necesitar un segundo intento.

Si no hay ningún modelo local listo cuando VOOL se inicia, comprueba hasta tres de los modelos de este ordenador,
empezando por el que elegiste, y usa el primero que la supera. Se salta un modelo demasiado grande para este
ordenador. Un modelo listo siempre va antes que uno que no lo está.

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
