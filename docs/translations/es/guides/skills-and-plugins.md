---
description: Amplía VOOL con habilidades y plugins.
translation: machine
source_sha256: a86860b8624e3c97c81e813ec92e3998c687a87d7bc43b7960c489cdcd309196
---

# Habilidades y plugins

Una **habilidad** es un conjunto empaquetado de instrucciones para una tarea recurrente. Un **plugin** añade
herramientas nuevas al entorno de ejecución.

## Incluidos frente a instalados externamente

VOOL incluye una **biblioteca de habilidades nativas** (las habilidades que vienen con la propia app) y,
en las versiones actuales, el plugin **VOOL Database**. También puedes instalar habilidades y plugins
propios.

| | Incluidos (vienen con VOOL) | Instalados externamente |
| --- | --- | --- |
| Dónde están | Dentro de la app | Tu carpeta de plugins (se configura en Ajustes) |
| Actualizaciones | Con la app | Cuando actualizas el paquete |
| Se pueden desactivar | Sí, de uno en uno | Sí, de uno en uno |
| Conceden permisos | **No** | **No** |

Dos reglas valen para ambos orígenes:

* **Las habilidades enseñan; nunca conceden.** Las herramientas que declara una habilidad son una señal de
  *restricción* (el presupuesto de herramientas del flujo de trabajo), nunca una concesión de nada. El controlador
  de permisos no lee las habilidades.
* **Un único inventario.** El catálogo que muestra la consola y los paquetes que carga el entorno de ejecución son
  la misma lista. Que una habilidad aparezca en una lista no prueba que pueda cargarse: cada entrada lleva su
  estado actual (disponible, desactivada, falta un requisito previo, contrato no válido) y el motivo.

## Encontrar habilidades

Habilidades y plugins muestra primero las habilidades propias de VOOL. **Explorar** incluye 53 habilidades de
VOOL listas para usar. Son solo de texto: cada una es un conjunto de instrucciones incluido con la app, así que
Explorar las muestra sin consultar nada en línea. Las habilidades de VOOL que usan herramientas de VOOL aparecen
en la pestaña **Códice de habilidades**, donde las habilidades del equipo de VOOL van primero. Revisa una
habilidad antes de activarla.

## Estados de disponibilidad

Una habilidad puede aparecer como no disponible con un motivo tipificado: falta un requisito previo (por ejemplo,
la habilidad de base de datos necesita el plugin de base de datos activado), una capacidad no disponible en este
equipo, un contrato no válido o desactivada por ti. Los motivos se muestran en cada entrada, y la desactivación
se hace por entrada y se mantiene entre reinicios.

## Identidades duplicadas

Si dos paquetes declaran la misma identidad de plugin (por ejemplo, un paquete incluido y una copia instalada),
se conserva el incluido de forma **determinista** y la copia descartada se indica en el
catálogo; nunca se oculta en silencio.

## Instalar los tuyos

Instala desde el catálogo de la app o apunta VOOL a una carpeta de plugins propia. Los paquetes llevan
un manifiesto (`plugin.json`) que declara sus herramientas; un manifiesto cuyas declaraciones de permisos no
coinciden con su clase de comportamiento declarada se rechaza al cargarlo.

{% hint style="danger" %}
Revisa el paquete completo antes de la verificación. Una revisión es una prueba sobre unos bytes concretos,
no un certificado de seguridad ni una concesión de permisos.
{% endhint %}

## Aprobación de paquetes en v0.7

La verificación cubre todos los archivos y directorios del paquete, incluidos los ejecutables de los manejadores,
las bibliotecas de terceros incluidas, los archivos de bloqueo de dependencias, las referencias, los recursos y los
bits de modo ejecutable. Añadir, quitar, renombrar o cambiar cualquiera de ellos invalida la aprobación. Se rechazan
los enlaces simbólicos, los enlaces duros, los archivos especiales, los modos privilegiados, las lecturas inestables
y las distribuciones demasiado grandes. Los límites de captura actuales son 4.096 entradas, 64 MiB por archivo y
128 MiB por paquete. No hay directorios de caché ni de estado ignorados dentro de un paquete.

Las aprobaciones guardadas anteriormente que solo contienen metadatos no activan paquetes en esta versión.
Revisa la distribución completa, verifícala de nuevo de forma explícita y después actívala. Las actualizaciones
siguen desactivadas hasta que se verifican y se activan; la aprobación nunca se migra en silencio.
Los complementos gestionados también necesitan una nueva revisión autenticada de la tienda cuando un recibo anterior
no vincula la distribución completa; la verificación normal no puede sustituir ese recibo.

`requires` nombra las dependencias de plugins instaladas. La verificación fija sus resúmenes criptográficos de
aprobación recursivos; cada dependencia ya debe estar verificada y activada. Una dependencia cambiada, actualizada,
desactivada o revocada deja no disponibles los paquetes que dependen de ella, también a través de herramientas en
caché. La admisión vuelve a comprobar el estado de activación y de revocación de todo el cierre capturado después de
calcular los hashes. Volver a verificar una dependencia no aprueba en silencio sus nuevos bytes para el paquete
padre: revisa y vuelve a verificar también cada padre. Cada `approval_digest` vincula el resumen de su paquete y las
aprobaciones de sus dependencias, así que una dependencia transitiva cambiada cambia esa aprobación aunque los
archivos propios del padre sigan idénticos. Una aprobación con dependencias cíclicas no puede activarse. El cierre
de dependencias está limitado a 64 paquetes.

Cada invocación registrada vuelve a comprobar el estado del ciclo de vida y captura copias privadas de solo lectura
del paquete y de su cierre de dependencias fijado. El contrato de la herramienta debe coincidir con el manifiesto
capturado. El manejador registrado se lanza desde esa captura; editar el código fuente después de la captura no
puede sustituir ese manejador ni sus archivos incluidos. Las observaciones de ejecución incluyen los resúmenes del
paquete, de la aprobación recursiva y de las dependencias. Los paquetes registrados requieren un confinamiento real
del kernel aunque un llamador local de bajo nivel haya elegido una anulación más débil.
Los equipos sin captura segura de descriptores sin seguir enlaces (no-follow) o sin confinamiento del kernel rechazan
los paquetes registrados. Los tiempos de espera de las invocaciones están limitados a 1–300 segundos; el grupo de
procesos propio de la invocación se detiene antes de eliminar su copia de ejecución.

Guarda la salida persistente, las cachés y los registros en `PLUGIN_SCRATCH`, no junto al código ejecutable.
Las herramientas de solo lectura no reciben ningún permiso de escritura. Las demás herramientas pueden escribir en
el espacio temporal, pero no pueden modificar su copia de ejecución. `VOOL_PLUGIN_DEPENDENCIES` es una
correspondencia JSON de las identidades de dependencia declaradas a los directorios de dependencias capturados.
Incluye las demás bibliotecas de terceros y sus archivos de bloqueo dentro del paquete; un archivo de bloqueo por
sí solo no certifica un entorno externo. El intérprete del equipo y las bibliotecas del sistema siguen formando
parte de la plataforma de confianza, en lugar de presentarse como bytes revisados de paquetes de terceros. Esta
comprobación de integridad no certifica el comportamiento, no certifica código arbitrario descargado o cargado desde
fuera de la distribución aprobada, no media en cada efecto interno ni sustituye la aprobación nativa del monedero.

## Escribir una habilidad

Una habilidad es una carpeta con un archivo de instrucciones y los archivos a los que hace referencia:

```text
my-skill/
├── SKILL.md
└── templates/
```

Crea un borrador en la app (`skill.create` lo prepara sin activar nada), valídalo y después instálalo para
activarlo: tres pasos separados a propósito, para que crear una habilidad nunca sea un cambio de comportamiento
implícito. El catálogo y el formato de creación aún se están terminando para su publicación.
