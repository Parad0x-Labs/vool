---
description: Amplía VOOL con habilidades y plugins.
translation: machine
source_sha256: 4dd2a317a7b10ee024616f6e0c27bd73087c44334f29e2c5f60f69773f4e54e8
---

# Habilidades y plugins

Una **habilidad** es un conjunto empaquetado de instrucciones para una tarea recurrente. Un **plugin** añade
herramientas, un proveedor de modelos o un analizador de complementos. Los plugins también pueden incluir habilidades.

## Incluidos frente a instalados externamente

VOOL incluye una **biblioteca de habilidades nativas** (las habilidades que vienen con la propia app) y,
en las versiones actuales, el plugin **VOOL Database**. También puedes instalar habilidades y plugins
propios.

| | Incluidos (vienen con VOOL) | Instalados externamente |
| --- | --- | --- |
| Dónde están | Dentro de la app | Tu carpeta de plugins (se gestiona desde la ventana de plugins de VOOL, no desde Ajustes) |
| Actualizaciones | Con la app | Cuando actualizas el paquete |
| Se pueden desactivar | Aún no hay un botón en las ventanas de la app | Sí, desde la ventana de plugins |
| Conceden permisos | **No** | **No** |

Dos reglas valen para ambos orígenes:

* **Las habilidades enseñan; nunca conceden.** Las herramientas que declara una habilidad son una señal de
  *restricción* (el presupuesto de herramientas del flujo de trabajo), nunca una concesión de nada. El controlador
  de permisos no lee las habilidades.
* **Un único inventario.** El catálogo que muestra la consola y los paquetes que carga el entorno de ejecución son
  la misma lista. Que una habilidad aparezca en una lista no prueba que pueda cargarse: cada entrada lleva su
  estado actual (disponible, desactivada, falta un requisito previo, contrato no válido) y el motivo.

## Encontrar habilidades

Habilidades y plugins muestra primero las habilidades propias de VOOL. **Explorar** incluye 75 habilidades de
VOOL listas para usar. Cada una es un conjunto de instrucciones incluido con la app, así que
Explorar las muestra sin consultar nada en línea. 22 de ellas usan herramientas de VOOL (tu correo, notas, archivos, PDF, monedero y
más) en los turnos en que se eligen: las herramientas de una habilidad solo restringen lo que VOOL ya ofrece para
tu petición, una herramienta desactivada sigue desactivada y cada llamada pide permiso como siempre. Su apartado
**Fuente y detalles técnicos** muestra las herramientas que usa. La pestaña **Códice de habilidades** muestra primero las
habilidades del equipo de VOOL. Revisa una habilidad antes de activarla.

Cómo se añade una habilidad de Explorar depende de quién la hizo:

* **Habilidades hechas por VOOL** (las 75 de arriba): pulsa **Añadir esta habilidad**. VOOL comprueba en tu
  ordenador que el archivo es exactamente el que venía con la app y lo lee en busca de instrucciones
  arriesgadas; después te muestra el resultado. No hace falta ninguna clave y no se envía nada a ningún sitio.
  Si el archivo no coincide, VOOL lo rechaza y lo indica.
* **Habilidades de otros creadores** (otros editores, o un enlace de GitHub que pegues): pulsa
  **Descargar y analizar**. VOOL descarga la habilidad pública en una versión fija y la comprueba en
  tu ordenador. Si activaste un plugin analizador, también recibe el contenido de la habilidad y
  su informe se añade junto al de VOOL. Lee el resultado y luego elige si añades la
  habilidad: si alguna de las comprobaciones informa de un problema, VOOL solo la añade después de que aceptes los riesgos de
  esa versión. No hace falta ninguna clave, salvo que tu plugin analizador pida una.

También puedes crear tu propia habilidad en el chat sin ninguna clave: consulta
[Crea tu propia habilidad](../../../guides/make-your-own-skill.md).

También puedes preguntar en cualquier chat: *¿qué habilidades tengo?* las muestra, y *¿cómo añado una
habilidad?* explica las dos formas.

## Activar o desactivar una habilidad

Abre **Habilidades y plugins**, luego **Mis complementos** y luego **Habilidades**. Cada habilidad muestra **Activado** o
**Desactivado** con un botón **Activar** o **Desactivar**. Una habilidad que desactivas no se usa en ningún chat
hasta que la vuelvas a activar. Sus archivos se conservan.

La tarjeta **Built-in skills** de **Extensiones** no tiene botón propio: activa o desactiva sus habilidades
una a una en **Habilidades**.

## Ver qué habilidad dio forma a una respuesta

Cuando las instrucciones de una habilidad formaron parte de cómo VOOL escribió una respuesta, una línea breve bajo la
respuesta lo indica, por ejemplo **Habilidad usada: Browser**. Aparece justo después de la respuesta, mientras
el chat está abierto; las respuestas que se cargan más tarde desde tu historial todavía no la muestran. Las reglas de estilo de respuesta
que acompañan a la mayoría de las respuestas no se nombran ahí.

## Habilidades para construir VOOL

La biblioteca también contiene las habilidades con las que el equipo de VOOL construye VOOL: revisión de código, ejecución de pruebas,
comprobaciones de publicación, reparación del pipeline y similares. No aparecen en el chat ni en la página de habilidades,
y no orientan las respuestas cotidianas. Los desarrolladores pueden mostrarlas y usarlas iniciando VOOL con
`VOOL_DEVELOPER_SKILLS=1`.

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

## Plugins en Complementos

Abre **Complementos → Mis complementos → Extensiones**. Revisa una carpeta local o una carpeta pública de GitHub en
un commit exacto. La instalación captura el paquete completo y lo deja desactivado. Los plugins firmados
de la tienda usan la cadena de registro existente y también se instalan desactivados. Revisa la hoja de acceso
por separado antes de activar herramientas o servicios. Guardar una clave de servicio no genera ninguna petición;
las pruebas de conexión y la actualización de la lista de modelos son acciones explícitas.

Desactivar quita las herramientas y detiene las llamadas en curso. Desinstalar primero desactiva, elimina los archivos propios
y el espacio temporal, borra por defecto las claves guardadas y conserva el historial. Las actualizaciones y reinstalaciones
requieren una revisión nueva y empiezan desactivadas. Una actualización desde otro repositorio o de otro editor
no conserva las claves guardadas. UsePod y Eyebrow ya no forman parte de VOOL, y VOOL
no ofrece ningún plugin para ninguno de los dos. Una clave guardada de UsePod o de Eyebrow sigue en este ordenador y VOOL
no la usa; Complementos lo indica hasta que cierres el aviso. Los chats que usaban un modelo de UsePod
no tienen ningún modelo seleccionado tras la actualización, y Auto no cambia a un modelo en la nube en esos
chats; un aviso lo indica hasta que lo cierres.

Consulta [Crear plugins](../../../guides/plugin-authoring.md) y [Comprobaciones de complementos](addon-security.md).

## Aprobación completa de paquetes

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
sí solo no certifica un entorno externo. El intérprete del entorno de ejecución y las bibliotecas del sistema siguen formando
parte de la plataforma de confianza, en lugar de presentarse como bytes revisados de paquetes de terceros. Esta
comprobación de integridad no certifica el comportamiento, no certifica código arbitrario descargado o cargado desde
fuera de la distribución aprobada, no media en cada efecto interno ni sustituye la aprobación nativa del monedero.

En macOS, el código de los plugins se ejecuta en el sandbox de macOS. Las claves llegan a un manejador solo por su entrada
privada, nunca por su entorno, porque cualquier programa del mismo usuario, incluido el código de los plugins,
puede leer el entorno de inicio de otro programa en un Mac. Un servidor de conexión que necesita una clave en su
entorno solo se ejecuta en el entorno de ejecución Linux aislado opcional, que preparas tú mismo;
VOOL nunca inicia una VM ni descarga una imagen. Sin él, ese plugin sigue desactivado y explica por qué.
Linux usa bwrap, con un espacio de nombres de procesos privado y `/proc`.

En macOS, una herramienta que declara acceso a la red solo puede llegar a un proxy privado que VOOL inicia para cada
llamada, y el proxy solo se conecta a los hosts declarados. En Linux y en el entorno de ejecución aislado,
los procesos de los plugins no tienen red. Las peticiones nativas de proveedores y analizadores usan su propio
transporte HTTPS hacia hosts declarados en todas las plataformas.

## Escribir una habilidad

* Para crear una habilidad pidiéndola en el chat, consulta [Crea tu propia habilidad](../../../guides/make-your-own-skill.md).
* Para escribir, revisar con lint, instalar y probar tú mismo un archivo SKILL.md, consulta [Crea y prueba una habilidad](../../../guides/build-and-test-a-skill.md).
