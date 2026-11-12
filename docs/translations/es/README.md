---
description: VOOL es un asistente de IA de escritorio que ejecuta modelos de IA en local en tu ordenador o se conecta a modelos en la nube con tu propia clave. Gratuito y privado. Descargas para Mac (Apple Silicon e Intel) y Linux; Windows es experimental y se instala desde el código fuente.
translation: machine
source_sha256: c31e5a047068e09c01bbd2e2c90cbbed0b70e78926847a5b737f9e0e625ab1dd
---

# VOOL

VOOL es un asistente de IA de escritorio. Se instala como una aplicación en tu ordenador y puede
ejecutar modelos de IA en local en esa máquina, o conectarse a un proveedor de IA en la nube con tu propia
clave API. Guarda un registro legible de cada acción que realiza.

Es gratuito y funciona sin conexión con modelos locales. Hay descargas para Mac (Apple Silicon
e Intel) y Linux x86_64; Windows es experimental y se instala desde el código fuente.

Esta documentación explica cómo instalar VOOL y conectar un modelo, y los conceptos que hay detrás de cómo
trata tus archivos, tu memoria y tus claves.

## Cómo se compara

En 150 preguntas de memoria de LoCoMo nunca usadas antes, con el mismo modelo respondiendo por cada agente y el
mismo evaluador GPT-4o, la memoria de VOOL superó a otros dos agentes de código abierto:

| | VOOL | OpenClaw | Hermes |
| --- | --- | --- | --- |
| Respuestas correctas | **82,7 %** (124 / 150) | 71,3 % (107) | 62,7 % (94) |
| Tokens por pregunta | **9,6k** | 51,8k | 33,5k |
| Coste por pregunta | **0,0033 $** | 0,0100 $ | 0,0046 $ |

Son 5,4 veces menos tokens que OpenClaw y 3,5 veces menos que Hermes, con un tercio del coste por respuesta
de OpenClaw. La memoria incluida en VOOL 0.7 obtuvo 123 de 150 en las mismas preguntas.
Cada ejecución, la configuración y los puntos débiles están publicados: [benchmarks de memoria](https://github.com/Parad0x-Labs/vool/tree/main/docs/benchmarks).

## Empieza aquí

| Si quieres | Lee |
| --- | --- |
| Poner VOOL en marcha | [Instalación](getting-started/install.md) |
| Entender la primera pantalla | [Primer inicio](getting-started/first-run.md) |
| Conectarlo a un modelo | [Conectar un modelo](getting-started/connect-a-model.md) |
| Controlar qué gastos pueden producirse | [Límites de gasto](../../guides/spending-limits.md) |
| Saber qué sale de tu equipo | [Local y nube](../../concepts/local-and-cloud.md) |

## Qué es VOOL

* **Una aplicación de escritorio.** Se instala en tu ordenador; no es una pestaña del navegador ni un
  servicio alojado.
* **Funciona sin conexión.** Con un modelo local seleccionado, VOOL no necesita conexión a internet.
* **Local de forma predeterminada.** Con un modelo local seleccionado, los prompts y los archivos se procesan
  en tu equipo.
* **Independiente del modelo.** Se pueden seleccionar tanto entornos de ejecución locales como proveedores en la
  nube. Tú aportas la clave de la nube, y se queda en tu equipo.
* **Inspeccionable.** Cada llamada a una herramienta se registra con sus entradas y resultados, para que una
  ejecución se pueda revisar después.

{% hint style="warning" %}
VOOL está en beta para Mac con Apple Silicon, Mac con Intel y Linux x86_64. Las apps para Mac
aún no están notarizadas por Apple, así que macOS te pide que confirmes la primera vez que abres una; la
página de [Instalación](getting-started/install.md#primera-apertura-en-un-mac) explica cómo. Consulta el
[Estado de las versiones](../../trust/release-status.md) antes de instalar.
{% endhint %}

## Dos modos, sin rodeos

VOOL distingue exactamente dos estados, y la interfaz siempre muestra cuál está activo:

1. **Local**: el trabajo se queda en este ordenador.
2. **Nube**: el contexto seleccionado se envía directamente al proveedor que hayas elegido,
   desde tu equipo y con tu clave.

No hay un tercer estado, ni ningún relé operado por Parad0x en medio. Los detalles
están en [Tratamiento de datos](trust/data-handling.md).

## Obtener ayuda

* [Preguntas frecuentes](../../help/faq.md): las dudas más habituales
* [Solución de problemas](help/troubleshooting.md): cuando algo no arranca
* [Discord](https://discord.gg/V9NkjP3Fzz): pregunta al equipo
