---
description: Cómo instalar el asistente de IA VOOL en macOS (Apple Silicon o Intel) o Linux desde las descargas de la versión, o desde el código fuente en macOS, Windows o Linux, con los requisitos del sistema y cómo verificar una descarga.
translation: machine
source_sha256: e0025d89fc56caca91923de6f1e6afe25ba6f6383a300ec3df5a7f7e3f401e55
---

# Instalación

VOOL 0.7.7-beta tiene descargas de la app para Mac con Apple Silicon, Mac con Intel y Linux x86_64 en
la [versión v0.7.7-beta](https://github.com/Parad0x-Labs/vool/releases/tag/v0.7.7-beta).
También puedes instalarlo desde el código fuente con el script de arranque en macOS, Linux o Windows.
Consulta el [Estado de las versiones](../../../trust/release-status.md) para los detalles de firma y actualizaciones.

## Requisitos

| | Mínimo | Holgado |
| --- | ---: | ---: |
| Memoria RAM | 8 GB | 16 GB o más |
| Disco | 2 GB para la app | 20 GB con modelos locales |
| macOS | 14 Sonoma, Apple Silicon o Intel ([límites](#mac-con-intel)) | 14 Sonoma o posterior, Apple Silicon |
| Windows | 10 (64 bits) | 11 |
| Linux | glibc 2.31 | Ubuntu 22.04 o posterior |

Los modelos locales necesitan bastante más memoria RAM que la propia app. Hay orientación sobre tamaños en
[Ejecutar un modelo local](../../../guides/local-models.md).

## Descargar la app

| Tu ordenador | Descarga |
| --- | --- |
| Mac con Apple Silicon (M1 y posteriores) | [`VOOL-0.7.7-beta-macos-arm64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-arm64.dmg) |
| Mac con chip Intel | [`VOOL-0.7.7-beta-macos-x86_64.dmg`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/VOOL-0.7.7-beta-macos-x86_64.dmg) |
| Linux x86_64 (Ubuntu 22.04 / 24.04) | [`vool-0.7.7-beta-linux-x86_64.tar.gz`](https://github.com/Parad0x-Labs/vool/releases/download/v0.7.7-beta/vool-0.7.7-beta-linux-x86_64.tar.gz) |

Para saber qué chip tiene tu Mac, abre el menú Apple y elige **Acerca de este Mac**. «Apple M…»
significa Apple Silicon; «Intel» significa Intel. La app para Apple Silicon no funciona en un Mac con Intel,
y lo indica al abrirla.

Comprueba el SHA-256 de la descarga antes de abrirla (consulta [Verificar una descarga](#verificar-una-descarga)).

### Primera apertura en un Mac

La app beta aún no está notarizada por Apple, así que macOS te pide que confirmes la primera vez
que la abres. Es lo esperado y solo ocurre una vez.

1. Abre el DMG y arrastra **VOOL** a **Aplicaciones**.
2. Haz doble clic en VOOL. macOS indica que no puede comprobar si la app contiene software malicioso.
   Cierra el mensaje.
3. Abre **Ajustes del Sistema → Privacidad y seguridad**. Desplázate hasta el mensaje sobre VOOL
   y haz clic en **Abrir igualmente**. Introduce tu contraseña para confirmar.

En macOS 14 Sonoma también puedes hacer clic en VOOL con la tecla Control pulsada en Aplicaciones, elegir
**Abrir** y después hacer clic de nuevo en **Abrir**.

### Linux

Descarga el archivo tar y su archivo `.sha256` de la versión, compruébalo y después ejecuta el
instalador incluido. No hace falta sudo ni el Python del sistema.

```bash
sha256sum -c vool-0.7.7-beta-linux-x86_64.tar.gz.sha256
tar -xzf vool-0.7.7-beta-linux-x86_64.tar.gz
bash vool-0.7.7-beta-linux-x86_64/install.sh
~/.local/bin/vool
```

VOOL se instala en `~/.local/share/vool`, pone el comando `vool` en `~/.local/bin` y añade
una entrada al menú de aplicaciones. La app de escritorio abre el chat en tu navegador; cerrar el navegador
no detiene VOOL. Para eliminar VOOL, ejecuta `bash ~/.local/share/vool/current/uninstall.sh`.
Tus chats y ajustes se quedan en tu perfil.

A partir de 0.7.5, la descarga para Linux se actualiza desde dentro de VOOL. La nueva versión se instala junto
a la anterior. Si no arranca bien, VOOL vuelve por sí solo a la anterior.

### Después de instalar

Antes de que un modelo local pueda responder, abre **Ajustes → Modelos y proveedores → Modelos locales** y pulsa **Certify** para ese modelo una vez. Ejecuta una breve comprobación en tu equipo.

A partir de 0.7.5, VOOL se actualiza solo: con «Conectarse por su cuenta» activado, muestra un aviso e instala una nueva versión con una sola pulsación. Si actualizas desde 0.7.4 o 0.7.3 en un Mac: activa «Conectarse por su cuenta» (Ajustes > Privacidad y permisos) y pulsa «Buscar actualizaciones» una vez. Desde 0.7.4 o anterior en Linux, o desde 0.7.2 y anteriores en un Mac: descarga 0.7.7 e instálala sobre tu versión actual. Tus chats y ajustes
están en tu perfil, fuera de la app. Las funciones de criptomonedas están desactivadas de forma predeterminada.

## Instalar desde el código fuente

Ejecuta el script de arranque para tu plataforma:

```bash
curl -fsSLo bootstrap_vool.sh https://raw.githubusercontent.com/Parad0x-Labs/vool/v0.7.7-beta/installer/bootstrap_vool.sh
bash bootstrap_vool.sh --ref v0.7.7-beta
```

```powershell
Invoke-WebRequest https://raw.githubusercontent.com/Parad0x-Labs/vool/main/installer/bootstrap_vool.ps1 -OutFile bootstrap_vool.ps1
powershell -ExecutionPolicy Bypass -File .\bootstrap_vool.ps1
```

Los comandos de macOS / Linux fijan tanto el script de arranque como su descarga del código fuente en `v0.7.7-beta`. Para elegir otra versión, cambia tanto la etiqueta en la URL del script como `--ref`. El script de arranque acepta un nombre de rama o una etiqueta de versión, no un hash de commit.

Instalar desde el código fuente requiere Python 3.11 o posterior. Si no encuentra ninguno, el instalador intenta
configurar uno. Las descargas de la app incluyen su propio Python.

El instalador prepara el entorno de Python, comprueba tu hardware, configura el modelo
local e inicia los servicios locales. Reserva tiempo y espacio en disco para la primera descarga del modelo.
La inferencia de los modelos locales funciona sin conexión después de la configuración; las herramientas web y los
modelos en la nube necesitan conexión.

## Mac con Intel

VOOL funciona en Mac con Intel y macOS 14 o posterior (por ejemplo, un MacBook Pro de 13 pulgadas de 2020),
con la descarga para Intel o desde el código fuente. El instalador desde el código fuente detecta la CPU Intel
e instala las versiones para Intel de todas las dependencias. Hay dos límites propios de Intel:

* **Sin entrenamiento de modelos locales.** PyTorch no ha publicado ninguna versión para macOS en Intel desde la 2.3,
  así que el instalador lo omite y el entrenamiento LoRA indica que no está disponible. El chat, la memoria,
  las herramientas y los modelos en la nube no se ven afectados.
* **Los modelos locales solo se ejecutan en la CPU.** Los motores de modelos locales no tienen aceleración por GPU
  para los gráficos Intel, así que las respuestas locales son varias veces más lentas que en Apple Silicon. En un
  Mac con 8 GB, usa un modelo en la nube para el chat; con 16 GB, el pequeño modelo local predeterminado del
  instalador funciona, pero es lento.

En los Mac con Intel, los modelos locales se ejecutan en el procesador y son lentos. Una respuesta larga de memoria puede agotar el tiempo de espera, así que de momento usa ahí un modelo en la nube. La certificación de modelos locales tiene en cuenta los procesadores más lentos.

Los Mac con Intel tienen su propia descarga, `VOOL-0.7.7-beta-macos-x86_64.dmg`. La descarga para Apple Silicon
no funciona en Intel y lo indica al iniciarse.

## Verificar una descarga

Cada descarga de la versión tiene al lado una suma de verificación SHA-256. Verifica el archivo antes
de abrirlo:

```bash
shasum -a 256 <downloaded-artifact>    # macOS
sha256sum <downloaded-artifact>         # Linux
```

```powershell
Get-FileHash -Algorithm SHA256 <downloaded-artifact>
```

Compara el resultado con la suma de verificación publicada con esa versión. Si no coinciden,
elimina el archivo y vuelve a descargarlo.

## Siguiente

Continúa con [Primer inicio](first-run.md).
