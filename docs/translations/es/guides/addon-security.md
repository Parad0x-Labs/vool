---
translation: machine
source_sha256: 021137e6424e28cf4af2913b10198aa3e59fb7d63a002949eeea0837375436a8
---
# Comprobaciones de complementos

Complementos mantiene separados explorar, inspeccionar e instalar. Un análisis describe unos bytes concretos;
no es una garantía de seguridad, un respaldo del editor, un permiso de ejecución ni una aprobación del monedero.

Explorar muestra primero las habilidades propias de VOOL, incluidas 75 habilidades de VOOL que vienen con la app (22 de ellas
usan herramientas de VOOL, que solo restringen lo que VOOL ya ofrece), y después los complementos revisados del catálogo. Códice de habilidades muestra primero las habilidades del equipo de VOOL, por encima de las
selecciones editoriales y las habilidades destacadas.

## Habilidades hechas por VOOL: la comprobación local

Las 75 habilidades hechas por VOOL vienen dentro de la app, cada una fijada por SHA-256 en el directorio propio de VOOL y
revisada cuando se publica VOOL. Para ellas, **Añadir esta habilidad** ejecuta solo la comprobación local de VOOL: sin clave,
sin cuota de análisis y sin ninguna petición de red.

1. VOOL lee la habilidad y la licencia de la copia propia de la app y las rechaza salvo que su SHA-256
   coincida con el valor fijado (*El archivo de esta habilidad de VOOL no coincide con la copia que venía con VOOL, así que no se añadió.*).
2. La comprobación local de VOOL lee la habilidad en busca de instrucciones arriesgadas y muestra el resultado. Un resultado limpio
   se puede añadir con **Añadir y activar**; cualquier cosa que señale necesita tu visto bueno aparte y explícito para
   esa versión exacta. **Rechazar** la descarta.
3. Después, la instalación sigue el mismo ciclo de vida que las demás habilidades: los archivos exactos se instalan,
   se verifican y se activan. La habilidad instalada se compara con su SHA-256 fijado cada vez que se
   carga; un archivo cambiado la deja no disponible.

La comprobación local es una lectura estática de unos bytes fijos, no una garantía de seguridad ni de calidad de las respuestas.
Las habilidades de otros creadores nunca siguen este camino.

## Habilidades públicas

Elige una habilidad pública de GitHub en una revisión fija y aprueba la descarga de su contenido. VOOL la comprueba
en este dispositivo. Sin un plugin analizador activado, la página explica
que las comprobaciones se quedaron en este dispositivo. Para este camino no hace falta ninguna clave de analizador.

Un plugin analizador activado también recibe el contenido de la habilidad pública cuando se solicita un análisis. Esto
puede consumir la cuota de su servicio. Los chats locales, los archivos privados del espacio de trabajo y otras credenciales no
se envían. Guardar una clave y abrir Complementos no hacen ninguna prueba de conexión ni petición de análisis. No hay
reintentos automáticos de las peticiones.

Lee los hallazgos y acepta la instalación por separado. La validación del informe requiere una cobertura completa
y hallazgos válidos. Un informe aprobado no tiene hallazgos ni infracciones de la política. Una aceptación del riesgo
ofrecida por separado vincula la versión revisada; nunca cambia el informe ni concede más acceso.
Se rechazan las revisiones caducadas, los bytes preparados cambiados, los enlaces y las carpetas de destino en conflicto.
Los recibos vinculan la distribución instalada completa. Los archivos cambiados retiran la disponibilidad en ejecución.
Las indicaciones de una habilidad se insertan completas o se omiten de forma explícita cuando no caben en el presupuesto de prompt.

## Analizadores opcionales

Un analizador remoto es opcional. Sin ningún plugin analizador activado, la comprobación propia de VOOL en este dispositivo
decide si una habilidad se puede instalar; las versiones anteriores exigían en su lugar un análisis remoto.

Para añadir uno, instala un plugin analizador de su creador desde la sección Extensiones, guarda tu propia
clave, revisa el acceso y actívalo. Su declaración nombra una ruta para una prueba de conexión explícita
y una ruta para los análisis solicitados, y el transporte nativo compartido de plugins hace esas peticiones.
VOOL no tiene un cliente analizador integrado ni un ajuste central de clave de analizador, y no ofrece ningún plugin analizador
propio. Una clave de Eyebrow guardada por una versión anterior de VOOL sigue en este ordenador y no se usa. Los
identificadores `vool-plugin-eyebrow` y `vool-plugin-usepod` están reservados; otro paquete que reclame
uno de ellos se rechaza antes de la revisión.

La comprobación propia de VOOL en este dispositivo siempre se ejecuta. El informe de un plugin analizador se añade junto a ella:
si cualquiera de los dos encuentra un problema, la instalación se bloquea, y el informe de un plugin debe nombrar el contenido exacto
que se comprobó. Un plugin analizador nunca puede aprobar una instalación por sí solo.

Un analizador activado cuya clave falta o se rechaza falla de forma clara. VOOL nunca sustituye en silencio
ese análisis remoto solicitado por un resultado local aprobado. Los tiempos de espera agotados, los errores del servicio, las redirecciones,
las respuestas demasiado grandes y los informes incompletos dejan la instalación sin aceptar. Una petición que agotó el tiempo de espera
puede haber consumido igualmente cuota del servicio.

## Paquetes ejecutables y revisión de la tienda

Los plugins usan una revisión completa del paquete, de una carpeta local o de un paquete público de GitHub fijado, separada del
importador de habilidades de solo texto. La instalación no ejecuta código del paquete y lo deja desactivado. Los plugins de la tienda
también requieren el recibo firmado existente y el resumen criptográfico de la distribución completa. La activación requiere una
hoja de acceso aparte que cubra herramientas, efectos, hosts exactos, ranuras de credenciales y servidores incluidos.

El código ejecutable recibe solo sus propias claves declaradas al invocarse. No puede leer las ranuras de otro
plugin, las claves de modelos de pago del propietario ni tiene autoridad sobre la firma, el límite, la aprobación o los recibos del monedero.
Su salida sigue sin ser de confianza. Los bytes cambiados retiran el consentimiento; desactivar detiene las llamadas. Un confinamiento
no compatible impide la ejecución. Consulta [Crear plugins](../../../guides/plugin-authoring.md) para ver los límites de ejecución.

Si VOOL no puede leer sus registros de plugins, los paquetes de los que no tiene registro dejan de ofrecer habilidades, salvo
las que vienen con VOOL, y el archivo dañado se conserva junto al nuevo. Si no puede leer
tus elecciones de activar y desactivar, todos los plugins cuentan como desactivados hasta que vuelvas a activar alguno.

## API local y transporte

`GET /api/addons` lee el inventario. Las modificaciones en `POST /api/addons` requieren acceso local del propietario,
JSON, peticiones del navegador del mismo origen y campos propios de cada acción. Los accesos de activación y de ciclo de vida de los plugins
(`POST /api/plugins/enable`, `POST /api/plugins/lifecycle`) aplican la misma regla de JSON y de
mismo origen. Las descargas de fuentes públicas usan
revisiones exactas y blobs verificados de tamaño limitado. Los servicios nativos requieren hosts HTTPS públicos declarados,
validan TLS, fijan las direcciones públicas resueltas, rechazan redirecciones, limitan las respuestas y se detienen cuando
el plugin se desactiva.

Los servicios HTTPS locales simulados pueden verificar el transporte y la gestión de errores sin llamadas de pago. No
demuestran la fiabilidad real del servicio, la facturación, la competencia del modelo ni la instalación dentro de otras
aplicaciones. Eso requiere una verificación aparte.
