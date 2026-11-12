---
translation: machine
source_sha256: 671f147e5d9649b6c2b7c2d391ea085120e783a8ace29f92ac8c3d5ecb4407eb
---
# Descubrir habilidades y análisis de Eyebrow

La página Habilidades y plugins tiene cuatro pestañas: **Explorar**, **Códice de habilidades**, **Mis complementos** y **Seguridad**. Explorar muestra primero las habilidades propias de VOOL, incluidas 53 habilidades de VOOL de solo texto que vienen con la app, después los complementos revisados del catálogo, y puede consultar un repositorio público de GitHub en una revisión fijada. Códice de habilidades muestra primero las habilidades del equipo de VOOL, por encima de las selecciones editoriales y las habilidades destacadas. La búsqueda ignora las mayúsculas y los separadores habituales; los controles de categoría y de orden acotan la lista. El origen, la licencia, la revisión y los requisitos se ven antes de descargar.

## Primer uso

1. Guarda una clave API de Eyebrow en la pestaña Seguridad, también disponible en Ajustes → Claves API. La clave queda en manos del almacén de credenciales existente. Guardarla no prueba la conexión ni analiza nada; Probar conexión contacta con Eyebrow de forma explícita.
2. En Explorar, elige una habilidad y aprueba descargar su contenido público y enviar ese contenido a Eyebrow. Esto consume la cuota de análisis del servicio según tu plan. No hay reintentos automáticos.
3. Lee el informe que se devuelve. Una respuesta HTTP correcta no es por sí sola un resultado limpio. Este catálogo inicial solo permite la instalación con un informe `pass` completo, sin hallazgos y sin infracciones de la política. Rechazar descarta la descarga preparada sin instalarla.
4. Acepta la instalación por separado. VOOL conserva la licencia, instala mediante su ciclo de vida de plugins existente, verifica el paquete exacto revisado y activa la habilidad. Los permisos existentes siguen controlando cada acción que pueda sugerir.

Un análisis es la evaluación de un proveedor sobre unos bytes concretos, no una garantía de seguridad, de compatibilidad ni de calidad de las respuestas. Estar incluido en el catálogo no es una certificación del editor.

## Límites

El contenido solo procede de repositorios públicos de GitHub en una revisión fija de 40 caracteres, con los archivos de la habilidad y de la licencia fijados por SHA-256. En esta versión no hay repositorios privados, extracción de archivos comprimidos, scripts de instalación ni importador de plugins ejecutables o MCP. Solo se envía a Eyebrow el SKILL.md independiente; los chats locales, los archivos del espacio de trabajo, las credenciales y el contenido de las licencias no se envían para su análisis.

Las descargas permanecen inertes hasta la revisión y la aceptación. Unos recibos autenticados localmente vinculan el informe al contenido fijado. La instalación rechaza las revisiones caducadas (24 horas), los archivos preparados modificados y los directorios de destino existentes. El entorno de ejecución comprueba el estado del ciclo de vida y los bytes revisados antes de añadir una habilidad gestionada al contexto del modelo. Los archivos añadidos, los enlaces simbólicos o el contenido cambiado la dejan no disponible. Las indicaciones se insertan completas o se registra de forma explícita que se omitieron por falta de presupuesto de prompt; las instrucciones analizadas no se truncan en silencio.

Estas comprobaciones no aíslan el código malicioso que ya se esté ejecutando como el usuario del sistema operativo. No se extienden a los plugins instalados manualmente con anterioridad. Un recibo de análisis no concede ningún permiso de ejecución nuevo ni ninguna autoridad sobre el monedero.

## API y gestión de fallos

`GET /api/addons` devuelve el catálogo y el estado local sin probar la conexión ni analizar nada. `POST /api/addons` admite `save_key`, `remove_key`, `test_key`, `scan`, `install` y `reject`. Las operaciones que modifican algo requieren peticiones por loopback, en JSON y, desde el navegador, del mismo origen; solo se aceptan los campos propios de cada operación. Se rechazan las URL de origen y las rutas que aporte quien llama.

El cliente usa la petición de Eyebrow v1 con el código de la habilidad incluido en `/v1/scan`, con autenticación bearer, un origen HTTPS fijo, sin redirecciones, con un tamaño de respuesta limitado y sin reintentos automáticos. `/v1/version` se usa para la prueba de conexión explícita. Referencia de la API: https://eyebrow.cc/api/reference .

Entre los fallos tipificados habituales están:

| Código | Significado / recuperación |
| --- | --- |
| `eyebrow_key_missing` / `eyebrow_unauthorized` | Guarda o sustituye la clave. |
| `eyebrow_quota` | Revisa la cuota del servicio antes de reintentar. |
| `eyebrow_timeout` / `eyebrow_unreachable` | Sin reintento automático; un análisis que agotó el tiempo de espera puede consumir cuota igualmente. |
| `eyebrow_invalid_report` / `eyebrow_no_coverage` | Las pruebas están incompletas; no se activa nada. |
| `addon_source_changed` / `addon_changed` | Los bytes fijados o revisados no coinciden; no los actives. |
| `review_untrusted` / `review_expired` | Obtén una revisión nueva antes de instalar. |
| `addon_already_installed` | Se conservaron los archivos existentes. La sustitución automática no está implementada. |

## Limitaciones actuales y verificación

No hay actualización automática, ni importador de catálogos arbitrarios, ni un flujo de reparación o reinstalación en el sitio para un paquete existente que haya cambiado. La recuperación de una revisión pendiente después de reiniciar la app no está disponible en la interfaz. Los textos nuevos de la interfaz se muestran de momento en inglés como alternativa; no se afirma que las traducciones estén completas.

Las pruebas automatizadas ejercitan el ciclo de vida real, la carga de habilidades, los manejadores de la API y la interfaz servida en el navegador, con descargas públicas y respuestas de Eyebrow simuladas de forma explícita. Demuestran el comportamiento del entorno de ejecución y de la interfaz, no la disponibilidad real de Eyebrow ni la competencia del modelo. Un análisis real con una clave y la aceptación en la app empaquetada nativa siguen siendo comprobaciones de la publicación. No hace falta ninguna clave ni ninguna llamada a un servicio de pago para ejecutar estas pruebas.
