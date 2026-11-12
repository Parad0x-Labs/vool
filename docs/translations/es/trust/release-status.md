---
description: En qué fase está VOOL, dicho sin rodeos.
translation: machine
source_sha256: bf79dfc262c1aca9c489c8474ad2de5fa633b0b60a515f2835f52954c66ad73f
---

# Estado de las versiones

Esta página es la referencia mantenida sobre el estado de las versiones. Otros documentos mencionan datos de las
versiones de pasada; cuando no coinciden con esta página, prevalece esta página.

## Fase actual

VOOL está en **beta**. La versión actual es
[**v0.7.7-beta**](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta), una versión de GitHub
compilada desde `main` en `8af687c`. Tiene descargas de la app para tres plataformas,
cada una con una suma de verificación SHA-256 publicada. La instalación desde el código fuente con el script de
arranque sigue siendo compatible en macOS, Linux y Windows; consulta [Instalación](../getting-started/install.md).

| Descarga | Para |
| --- | --- |
| `VOOL-0.7.7-beta-macos-arm64.dmg` | Mac con Apple Silicon, macOS 14 o posterior |
| `VOOL-0.7.7-beta-macos-x86_64.dmg` | Mac con Intel, macOS 14 o posterior |
| `vool-0.7.7-beta-linux-x86_64.tar.gz` | Linux x86_64, probado en Ubuntu 22.04 y 24.04 |

Los registros de versiones de este repositorio cubren el canal de GitHub. Otros canales de descarga se
gestionan por separado; verifica cualquier descarga con la suma de verificación publicada en la versión
de GitHub.

| | Estado |
| --- | --- |
| Descargas públicas | DMG para macOS con Apple Silicon, DMG para macOS con Intel y archivo tar para Linux x86_64 en la versión v0.7.7-beta de GitHub |
| Sumas de verificación publicadas | SHA-256 de cada descarga, en la versión |
| Firma del código | Las apps para macOS tienen una firma ad hoc, no Developer ID. La firma se añade después de esta beta y no la condiciona |
| Notarización de macOS | Sin notarizar. macOS te pide que confirmes la primera apertura (ver abajo) |
| Windows | No hay descarga para Windows en esta versión; Windows se instala desde el código fuente y es experimental |
| Actualizaciones | A partir de 0.7.5, VOOL se actualiza solo: con «Conectarse por su cuenta» activado, muestra un aviso e instala una nueva versión con una sola pulsación. Si actualizas desde 0.7.4 o 0.7.3 en un Mac: activa «Conectarse por su cuenta» (Ajustes > Privacidad y permisos) y pulsa «Buscar actualizaciones» una vez. En Linux, o desde 0.7.2, 0.7.1 o 0.7.0: descarga 0.7.7 e instálala sobre tu versión actual. Tus chats y ajustes están en tu perfil, fuera de la app. Consulta [Actualizaciones y migración](../reference/updates-migration.md) |
| Estabilidad de la API | Cambia entre versiones |

## Qué significa para ti

* La primera vez que abres una app para Mac, macOS se niega a abrirla porque no está notarizada.
  Es lo esperado en esta fase, no una señal de que la descarga esté dañada. Comprueba la suma de verificación
  y después abre **Ajustes del Sistema → Privacidad y seguridad** y haz clic en **Abrir igualmente**. En
  macOS 14 también puedes hacer clic en la app con la tecla Control pulsada y elegir **Abrir**.
* Elige la descarga para Mac que corresponda a tu chip. La app para Apple Silicon no funciona en un
  Mac con Intel y lo indica al iniciarse.
* Los formatos de configuración pueden cambiar entre versiones.
* Las funciones de criptomonedas están desactivadas hasta que las activas.

## Limitaciones de la beta y del piloto, dichas explícitamente

| Capacidad | Estado en esta versión |
| --- | --- |
| Criptomonedas y monedero | Desactivados de forma predeterminada. Cuando los activas, VOOL usa mainnet; las redes de prueba están en Developer options. Cada pago pasa por el monedero de VOOL, con sus límites, tu aprobación y un recibo. Consulta [Monedero](../guides/wallet.md). |
| Transferencias en mainnet | Monedas nativas, y USDT en Solana, Ethereum, BNB Smart Chain y Arbitrum One, enviadas desde un VOOL Wallet creado en este dispositivo. USDC en Solana y Base es solo para canales de pago. Una cuenta conectada o de solo lectura puede recibir y mostrar su saldo, pero no puede enviar desde VOOL. |
| Pagar con x402 | Los sitios web que piden un pago x402 en USDC en Base o Base Sepolia pueden cobrarse desde tu monedero Crypto Pilot: una aprobación con tu PIN, dentro de tus límites. El servicio de pago del sitio web cubre la comisión de red. **Ajustes → Cripto → Payment channels** tiene un interruptor para cada forma en que el monedero puede pagar a un sitio web; un canal desactivado rechaza el pago antes de firmar nada. Todavía no se ha hecho ningún pago real en mainnet con esto. Pagar mediante Solana pay-kit no está incluido en las descargas; ese canal aparece como no utilizable. |
| X | Desde cualquier chat: busca publicaciones y cuentas, Spaces, tendencias y mensajes directos, y consulta los calls anteriores sobre un token. Todo lo que actúa sobre tu cuenta espera tu confirmación. |
| Dictado | No disponible temporalmente en esta versión beta; volverá en una actualización. |
| Modo de consulta «ráfaga» en la nube | La confirmación por llamada aún no está conectada en esta versión. |
| Modelos locales | Antes de que un modelo local pueda responder, abre **Ajustes → Modelos y proveedores → Modelos locales** y pulsa **Certify** para ese modelo una vez. |
| Mac con Intel | Los modelos locales se ejecutan en el procesador y son lentos. La certificación tiene en cuenta los procesadores más lentos, pero una respuesta larga de memoria puede agotar el tiempo de espera, así que de momento usa ahí un modelo en la nube. |
| Chat privado | Di «private chat», «don't record this» u «off the record», y no se guarda nada de ese chat. Los comandos funcionan en los 17 idiomas de la app. |
| Olvidar | Olvidar borra la memoria de VOOL y el historial de ejecución correspondiente, para que el elemento no se vuelva a recordar ni a listar, y borra las palabras olvidadas del archivo de memoria local de VOOL y de su índice de búsqueda. VOOL guarda las palabras que le pediste olvidar en una lista corta en tu ordenador, para poder seguir rechazándolas. Después de olvidar algo en un chat, VOOL no volverá a aceptar ese mismo dato en ese chat; empieza un chat nuevo para decírselo otra vez. |
| PDF escaneados | Se leen con el reconocimiento de texto integrado del Mac, así que solo en Mac. |
| Mascota de escritorio | La mascota vive en la ventana de VOOL. Arrastrarla fuera de la ventana no la mueve al escritorio. |
| Códice de habilidades | Explorar el códice de habilidades muestra las habilidades publicadas para tu versión exacta de VOOL, y revisas una habilidad antes de que se instale. Publicar en el códice de habilidades comprueba una habilidad que creaste en tu ordenador y después abre el sitio web de Skill Codex con ella ya rellenada; allí inicias sesión y pulsas Send, y VOOL no envía nada por sí mismo. Solo se envía el texto de la habilidad, así que todavía no se puede compartir una habilidad con archivos adicionales. |

La firma con Developer ID y la notarización para macOS, y las sumas de verificación firmadas para Linux, son los
siguientes pasos del camino de publicación; hoy no se afirma ninguna de las dos cosas.
