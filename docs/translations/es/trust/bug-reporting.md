---
description: Informar de un error de forma segura: borradores depurados, aprobación de los bytes exactos y lo que nunca sale de tu equipo.
translation: machine
source_sha256: 4e64e6313c1f6a8be6784a1f35cedc87b81a5661fdb548c6821109f935db818f
---

# Informes de errores y privacidad

## El informador de errores integrado

Usa el informador de errores de la app (o la CLI `python -m apps.vool_cli bug-report`). Nunca envía nada por
sí mismo: el proceso tiene deliberadamente tres fases:

1. **Borrador**: se crea un informe en local a partir de los registros y diagnósticos recientes, depurado a medida
   que se crea. *«Nothing is sent yet — a local draft is created first.»*
2. **Vista previa**: ves los **bytes exactos** que se enviarían, con un hash de la carga útil y un resumen de la
   depuración que lista lo que se quitó. Los campos que no quieras enviar (registros, indicadores, notas para
   reproducirlo, adjuntos) se pueden quitar; cualquier edición restablece la aprobación.
3. **Aprobar**: enviar requiere tu aprobación explícita de esos bytes exactos. Si la carga útil
   cambia, la aprobación deja de valer y tienes que volver a aprobar.

Después de aprobar, **Open on GitHub** abre en tu navegador una página de informe ya rellenada en el repositorio
público [vool-reports](https://github.com/Parad0x-Labs/vool-reports). Léela allí una vez más y pulsa Submit. VOOL
no envía nada por sí mismo, y no hace falta ningún token de GitHub.
**Copy report** y **Save a local copy** no necesitan ninguna aprobación: te dan los mismos bytes saneados
y nada sale de tu equipo. **My reports** lista tus borradores y cómo salió cada uno.

## Informar de una respuesta incorrecta

Pulsa **Reportar** o 👎 bajo cualquier respuesta, o resalta la parte incorrecta y pulsa **Reportar**. El
informe cita solo esa parte (o la respuesta completa). Tu pregunta no se incluye salvo que marques la
casilla. Se incluye el recibo de la respuesta: modelo, proveedor, tokens, tiempo y qué pasos se ejecutaron, nunca
su contenido. Una respuesta que pediste a VOOL que olvidara no se puede reportar.

## Qué se añade para ayudar a investigar

Las características de tu ordenador: versión del sistema operativo, chip, memoria, GPU y la versión del servidor
de modelos local. También una breve comprobación de estado: si el servidor de modelos local responde, y el espacio
libre en disco como un rango. Nunca el nombre de tu ordenador, tu nombre de usuario, números de serie ni
direcciones de red. Puedes quitar cualquiera de estos datos en la vista previa.

## Qué elimina la depuración

De forma determinista, antes de que llegues a ver el borrador: credenciales y cabeceras de autenticación, rutas de
la carpeta personal y nombres de usuario, el nombre de tu ordenador, correos electrónicos, direcciones IP, PIN y
códigos de un solo uso, números de tarjeta y de cuenta bancaria (IBAN), números de teléfono, direcciones de
monedero, los datos personales guardados en tu perfil, secretos etiquetados y bloques de alta entropía, todo ello
reforzado con una pasada final de búsqueda de secretos.
VOOL no puede reconocer nombres ni datos privados escritos en frases normales, así que lee la vista previa. Las
trazas de pila se reconstruyen a partir de una lista de elementos permitidos, no se pegan tal cual.
Las pruebas de fallos de los informes llevan **solo códigos tipificados**; nunca contexto interno ni cadenas de
causas.

Cada valor eliminado se sustituye por un marcador. Un correo electrónico, una persona, una carpeta personal, una
dirección IP, un número de teléfono, una dirección de monedero, el nombre de tu ordenador o un dato de tu perfil
mantienen un mismo marcador numerado en todo el informe, como `[EMAIL_1]` y `[EMAIL_2]` para dos direcciones
distintas, para que el informe se siga leyendo con claridad.
Cada clave, contraseña, PIN o número de tarjeta o de cuenta bancaria eliminado aparece como `[SECRET_REMOVED]`,
nunca numerado. La lista que vincula un marcador con el valor real se guarda solo en la memoria RAM de VOOL
mientras trabajas en el informe, para que una edición conserve los mismos marcadores. Nunca se guarda ni se envía,
y se olvida cuando eliminas el informe, tras una hora sin usarse o cuando VOOL se reinicia.

## Qué no hace nunca VOOL

* Ningún envío automático de informes al exterior, de ningún tipo.
* Ningún canal de telemetría esperando detrás de un ajuste.
* Los problemas de seguridad van por el canal privado de la
  [política de seguridad](https://github.com/Parad0x-Labs/vool/security/policy) del repositorio, nunca por una
  incidencia pública.

## Resumen de privacidad

* Con un modelo local seleccionado, los prompts y los archivos se procesan en tu equipo.
* Las llamadas a la nube usan la clave API que proporcionaste, guardada en local; las claves nunca viajan en los
  informes.
* El detalle completo del flujo de datos está en [Tratamiento de datos](../trust/data-handling.md).
