---
description: Cómo está construido VOOL: la arquitectura en una página, la configuración para desarrolladores y cómo contribuir.
translation: machine
source_sha256: 7cb91029e16c6510821e1ca26b509da8cdfb63113111619d3a31e364a2f088d3
---

# Arquitectura y contribución

## La arquitectura en una página

VOOL es un entorno de ejecución de escritorio que prioriza lo local, con un núcleo servido:

```text
┌──────────────────────────────────────────────┐
│ Native window / browser console              │
│  (served pages; the console you use)         │
├──────────────────────────────────────────────┤
│ Served runtime (local HTTP API)              │
│  routing → tool execution → answers          │
├───────────────┬──────────────┬───────────────┤
│ Local models  │ Cloud lanes  │ Wallet (opt)  │
│ (Ollama etc.) │ (your keys)  │ pilot lanes   │
├───────────────┴──────────────┴───────────────┤
│ Records: receipts, ledgers, fault registry   │
│ Local storage (SQLite under your profile)    │
└──────────────────────────────────────────────┘
```

Principios que impone el código:

* **Local de forma predeterminada; la nube, con tu clave.** Las vías en la nube solo existen con tus credenciales y
  tus topes.
* **Una autoridad por decisión.** Los permisos, el gasto y la salida a la red tienen cada uno un único
  contrato responsable; los fallos se clasifican en la frontera que los gestiona.
* **Decir qué falló.** Los resultados desconocidos se tipifican como desconocidos; los rechazos dicen qué no
  ocurrió; un modelo fijado nunca se sustituye en silencio.
* **Todo queda registrado.** Las llamadas a herramientas, las aprobaciones, los pagos y las actualizaciones escriben
  recibos en tu perfil.

## Configuración para desarrolladores

```bash
git clone https://github.com/Parad0x-Labs/vool.git
cd vool
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests -q            # the suite
```

Las notas de cada plataforma, la organización completa de las herramientas y los controles de publicación están en
la documentación de ingeniería del repositorio (`docs/` en la raíz del repositorio; los detalles internos más
profundos no forman parte de este sitio público de documentación).

## Documentación: una sola fuente canónica

Esta documentación se genera a partir de **una** fuente: la carpeta `docs/` de este repositorio (estructura de
GitBook, `.gitbook.yaml` en la raíz del repositorio). El sitio público de documentación se construye a partir de
ella. Para trabajar en la documentación:

```bash
python3 tools/docs/build_docs.py --src . --out docs-build --base /docs
python3 tools/docs/check_docs_build.py docs-build /docs
python3 -m http.server -d docs-build 8000   # local preview
```

La referencia de errores (`ERROR_BOOK.md`) y el manifiesto de entrega para la localización se
**generan a partir de los propios registros del entorno de ejecución**: vuelve a generarlos, nunca los edites a mano:

```bash
python -m tools.generate_error_book
python -m tools.generate_localization_handoff
```

Una prueba específica hace fallar la compilación si los archivos generados se desvían de sus registros.

## Contribuir

* Cambios pequeños y revisados; cada afirmación de la documentación debe coincidir con el código tal como se publica.
* Conserva los códigos y los identificadores estables: duran más que las versiones.
* Nunca amplíes una autoridad en silencio: un permiso nuevo, una vía de gasto o una vía de salida a la red es un
  cambio de diseño, no un parche.
* Las limitaciones de la beta se documentan, no se ocultan: si encuentras una sin documentar, es un
  error en la documentación.
