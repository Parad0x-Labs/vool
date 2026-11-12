---
translation: machine
source_sha256: 72ade2152db88157a95e2155800ad7f7ecbca477e44e40d1aac1d95495bbc42e
---

# Libro de errores de VOOL

<!-- GENERATED FILE — do not hand-edit. Regenerate with `python -m tools.generate_error_book`. -->

<!-- GENERATED FILE: the app's own texts for each code, and the words in tools/docs/error_book_words.json. Regenerate with tools/docs/translate_error_book.py, never by hand. -->

Todos los errores que VOOL puede mostrarte, generados a partir del propio catálogo de errores de la app: los códigos, mensajes y pasos siguientes que aparecen abajo son exactamente lo que muestra la app.

- **Esquema:** `vool.fault.v1`
- **Versión del catálogo:** `1`
- **Resumen del catálogo:** `83290d22af3092c2aee6c9a3a5fe466107140dd7d52ab2aab1e8d817dc548b64`
- **Número de fallos:** 58

Cada código es fijo y nunca se reutiliza con otro significado. Cada código tiene su propia sección más abajo, cuyo encabezado es el propio código, así que, si buscas en esta página el código tal como te lo dieron, encuentras su sección, y un enlace a la sección es la dirección de esta página seguida de `#` y del código con guiones en lugar de guiones bajos. Cada sección dice qué pasó, qué puede haber pasado ya, qué hacer a continuación y si volver a intentarlo puede ayudar. Si un código que te dieron no está en este libro, no es de esta versión de VOOL: comprueba qué versión lo mostró o notifícalo con el código tal como está escrito.

## Seguridad

| Código | Título | Gravedad | Qué puede haber pasado ya |
|---|---|---|---|
| [`confinement_refusal`](#confinement-refusal) | Cambio fuera del alcance permitido | alta | No se envió, firmó ni cambió nada |
| [`credential_failure`](#credential-failure) | No se pudo leer una credencial guardada | alta | No se envió, firmó ni cambió nada |
| [`wallet_backup_unavailable`](#wallet-backup-unavailable) | Copia de respaldo ya mostrada una vez | alta | No se envió, firmó ni cambió nada |
| [`wallet_caller_refused`](#wallet-caller-refused) | No se inició desde la propia ventana de VOOL | alta | No se envió, firmó ni cambió nada |
| [`wallet_card_data_refused`](#wallet-card-data-refused) | Datos de tarjeta rechazados | alta | No se envió, firmó ni cambió nada |
| [`wallet_chain_identity_mismatch`](#wallet-chain-identity-mismatch) | Identidad del punto de la cadena no probada | alta | Una solicitud llegó a un servicio externo; no se realizó ningún pago |
| [`wallet_environment_inactive`](#wallet-environment-inactive) | Entorno de redes incorrecto | media | No se envió, firmó ni cambió nada |
| [`wallet_export_pin_not_accepted`](#wallet-export-pin-not-accepted) | La exportación solo se desbloquea con la autenticación del dispositivo | alta | No se envió, firmó ni cambió nada |
| [`wallet_export_refused`](#wallet-export-refused) | Antigua forma de exportar retirada | alta | No se envió, firmó ni cambió nada |
| [`wallet_export_unavailable`](#wallet-export-unavailable) | Exportación no disponible para este monedero | alta | No se envió, firmó ni cambió nada |
| [`wallet_legacy_surface_retired`](#wallet-legacy-surface-retired) | Camino de dinero heredado retirado | alta | No se envió, firmó ni cambió nada |
| [`wallet_network_disabled`](#wallet-network-disabled) | Red no declarada para este monedero | alta | No se envió, firmó ni cambió nada |
| [`wallet_outbound_refused`](#wallet-outbound-refused) | Solicitud fuera de los destinos aprobados | alta | No se envió, firmó ni cambió nada |
| [`wallet_quote_mismatch`](#wallet-quote-mismatch) | La aprobación ya no coincide con la vista previa | alta | No se envió, firmó ni cambió nada |
| [`wallet_recovery_refused`](#wallet-recovery-refused) | La prueba de recuperación no encaja con este monedero | alta | No se envió, firmó ni cambió nada |
| [`wallet_signature_invalid`](#wallet-signature-invalid) | La firma no coincidía con la clave del monedero | alta | Una solicitud llegó a un servicio externo; no se realizó ningún pago |
| [`wallet_unlock_throttled`](#wallet-unlock-throttled) | Monedero bloqueado tras desbloqueos fallidos | alta | No se envió, firmó ni cambió nada |

## Integridad

| Código | Título | Gravedad | Qué puede haber pasado ya |
|---|---|---|---|
| [`evidence_corruption`](#evidence-corruption) | Los registros de la respuesta fallaron una comprobación de consistencia | alta | Se produjo un registro o un cambio local |
| [`integrity_verification_failure`](#integrity-verification-failure) | Un registro falló su comprobación de integridad | crítica | Se produjo un registro o un cambio local |
| [`unsupported_claim`](#unsupported-claim) | Afirmaciones sin respaldo retenidas | media | Se produjo un registro o un cambio local |
| [`wallet_duplicate_payment`](#wallet-duplicate-payment) | Pago ya propuesto o enviado | media | Se produjo un registro o un cambio local |

## Política

| Código | Título | Gravedad | Qué puede haber pasado ya |
|---|---|---|---|
| [`evm_pocket_custody_unavailable`](#evm-pocket-custody-unavailable) | Bolsillo heredado solo para Solana | baja | No se envió, firmó ni cambió nada |
| [`permission_denied`](#permission-denied) | Permiso denegado | media | No se envió, firmó ni cambió nada |
| [`wallet_acknowledgement_required`](#wallet-acknowledgement-required) | Confirmación requerida | baja | No se envió, firmó ni cambió nada |
| [`wallet_amount_invalid`](#wallet-amount-invalid) | Importe no exacto para esta moneda | baja | No se envió, firmó ni cambió nada |
| [`wallet_approval_rejected`](#wallet-approval-rejected) | Aprobación no aceptada | media | No se envió, firmó ni cambió nada |
| [`wallet_confirmation_required`](#wallet-confirmation-required) | Confirmación escrita requerida | baja | No se envió, firmó ni cambió nada |
| [`wallet_credential_mismatch`](#wallet-credential-mismatch) | Las entradas no coincidían | baja | No se envió, firmó ni cambió nada |
| [`wallet_dependency_unavailable`](#wallet-dependency-unavailable) | Bibliotecas EVM no instaladas | media | No se envió, firmó ni cambió nada |
| [`wallet_device_auth_denied`](#wallet-device-auth-denied) | Desbloqueo con el dispositivo rechazado | baja | No se envió, firmó ni cambió nada |
| [`wallet_disabled`](#wallet-disabled) | El monedero está apagado | baja | No se envió, firmó ni cambió nada |
| [`wallet_insufficient_funds`](#wallet-insufficient-funds) | El saldo no cubre el importe y la comisión | baja | Una solicitud llegó a un servicio externo; no se realizó ningún pago |
| [`wallet_limit_exceeded`](#wallet-limit-exceeded) | Límite de gasto superado | media | No se envió, firmó ni cambió nada |
| [`wallet_not_found`](#wallet-not-found) | Monedero, propuesta o destino no reconocido | baja | No se envió, firmó ni cambió nada |
| [`wallet_password_invalid`](#wallet-password-invalid) | Contraseña del monedero no válida | baja | No se envió, firmó ni cambió nada |
| [`wallet_paykit_unavailable`](#wallet-paykit-unavailable) | Extra pay de Solana no instalado | media | No se envió, firmó ni cambió nada |
| [`wallet_payment_channel_off`](#wallet-payment-channel-off) | Canal de pago desactivado | baja | No se envió, firmó ni cambió nada |
| [`wallet_pin_invalid`](#wallet-pin-invalid) | PIN no válido | baja | No se envió, firmó ni cambió nada |
| [`wallet_quote_expired`](#wallet-quote-expired) | La vista previa caducó o fue sustituida | baja | No se envió, firmó ni cambió nada |
| [`wallet_recipient_refused`](#wallet-recipient-refused) | El destinatario no puede recibir esto con seguridad | media | No se envió, firmó ni cambió nada |
| [`wallet_request_ambiguous`](#wallet-request-ambiguous) | Encaja más de una cuenta o red | baja | No se envió, firmó ni cambió nada |
| [`wallet_send_needs_vool_wallet`](#wallet-send-needs-vool-wallet) | Para enviar hace falta un VOOL Wallet | baja | No se envió, firmó ni cambió nada |
| [`wallet_setup_state_invalid`](#wallet-setup-state-invalid) | El paso de configuración no se aplica ahora | baja | No se envió, firmó ni cambió nada |
| [`wallet_signing_unavailable`](#wallet-signing-unavailable) | El monedero no puede firmar desde aquí | media | No se envió, firmó ni cambió nada |
| [`wallet_storage_class_refused`](#wallet-storage-class-refused) | Almacenamiento de claves solo en memoria | media | No se envió, firmó ni cambió nada |
| [`wallet_x402_cap_exceeded`](#wallet-x402-cap-exceeded) | Recurso de pago por encima del tope automático | media | No se envió, firmó ni cambió nada |
| [`wallet_x402_sdk_unavailable`](#wallet-x402-sdk-unavailable) | Extra x402 no instalado | media | No se envió, firmó ni cambió nada |
| [`x402_scheme_unavailable`](#x402-scheme-unavailable) | Esquema de pago no admitido | baja | No se envió, firmó ni cambió nada |

## Disponibilidad

| Código | Título | Gravedad | Qué puede haber pasado ya |
|---|---|---|---|
| [`provider_exhausted`](#provider-exhausted) | Límite del proveedor alcanzado | media | Una solicitud llegó a un servicio externo; no se realizó ningún pago |
| [`provider_unavailable`](#provider-unavailable) | Proveedor inaccesible | media | No se envió, firmó ni cambió nada |
| [`timeout`](#timeout) | Tiempo de espera agotado | media | Resultado desconocido: comprueba el estado antes de reintentar |
| [`tool_unavailable`](#tool-unavailable) | Herramienta no disponible | baja | No se envió, firmó ni cambió nada |
| [`wallet_broadcast_failed`](#wallet-broadcast-failed) | No se pudo difundir el pago | media | Resultado desconocido: comprueba el estado antes de reintentar |
| [`wallet_device_auth_unavailable`](#wallet-device-auth-unavailable) | Desbloqueo con el dispositivo no disponible | media | No se envió, firmó ni cambió nada |
| [`wallet_quote_unavailable`](#wallet-quote-unavailable) | Saldo o comisión desconocidos | media | Una solicitud llegó a un servicio externo; no se realizó ningún pago |
| [`wallet_simulation_failed`](#wallet-simulation-failed) | El pago no pasó la simulación | media | Una solicitud llegó a un servicio externo; no se realizó ningún pago |

## Cancelación

| Código | Título | Gravedad | Qué puede haber pasado ya |
|---|---|---|---|
| [`cancelled`](#cancelled) | Cancelado | informativa | Resultado desconocido: comprueba el estado antes de reintentar |

## Internos

| Código | Título | Gravedad | Qué puede haber pasado ya |
|---|---|---|---|
| [`unknown`](#unknown) | Error inesperado | media | Resultado desconocido: comprueba el estado antes de reintentar |

## Referencia por código

Todos los códigos, en orden alfabético. El encabezado de la sección es el ancla fija del código.

<a name="cancelled"></a>

### cancelled

**Cancelado**

- **Qué pasó:** Esto se canceló, así que no terminó.
- **Qué puede haber pasado ya:** Resultado desconocido: comprueba el estado antes de reintentar
- **Qué hacer a continuación:** No hace falta nada; vuelve a empezar si quieres que se haga.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** informativa · **Categoría:** cancelación

<a name="confinement-refusal"></a>

### confinement_refusal

**Cambio fuera del alcance permitido**

- **Qué pasó:** Un cambio de archivo fuera del alcance permitido fue bloqueado. Nada fuera del alcance se tocó.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Si este cambio es deseado, pídelo explícitamente para que quede dentro del alcance autorizado.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** alta · **Categoría:** seguridad · **Relevante para la seguridad:** sí

<a name="credential-failure"></a>

### credential_failure

**No se pudo leer una credencial guardada**

- **Qué pasó:** No se pudo leer una credencial guardada. Vuelve a introducirla en ajustes para continuar.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** La entrada del cofre no descifró; vuelve a guardar la credencial o comprueba la clave del cofre.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** alta · **Categoría:** seguridad · **Relevante para la seguridad:** sí

<a name="evidence-corruption"></a>

### evidence_corruption

**Los registros de la respuesta fallaron una comprobación de consistencia**

- **Qué pasó:** Los registros que respaldan esta respuesta fallaron una comprobación de consistencia; trata la respuesta como no verificada.
- **Qué puede haber pasado ya:** Se produjo un registro o un cambio local
- **Qué hacer a continuación:** Investiga la contabilidad de ejecución de este turno contra el flujo de eventos del entorno.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** alta · **Categoría:** integridad · **Relevante para la seguridad:** sí

<a name="evm-pocket-custody-unavailable"></a>

### evm_pocket_custody_unavailable

**Bolsillo heredado solo para Solana**

- **Qué pasó:** Un bolsillo heredado, el monedero basado en una frase de recuperación, solo guarda una clave de Solana, así que no se puede crear ni restaurar en una red EVM. No se creó nada. Para una red EVM, crea una Cartera VOOL en esa red en Ajustes, Cripto, añade la cuenta en modo de solo lectura o conecta tu propio monedero EVM.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** El bolsillo heredado sella una sola semilla de Solana de su frase de recuperación. En una red EVM, usa un monedero Crypto Pilot («Create wallet» en esa red, en Ajustes, Cripto, Redes y carteras), una cuenta de solo lectura o un monedero EVM externo (EIP-1193) que guarda su propia clave.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** baja · **Categoría:** política

<a name="integrity-verification-failure"></a>

### integrity_verification_failure

**Un registro falló su comprobación de integridad**

- **Qué pasó:** Un registro falló su comprobación de integridad. No se aceptó nada en silencio.
- **Qué puede haber pasado ya:** Se produjo un registro o un cambio local
- **Qué hacer a continuación:** Trata la cadena de recibos afectada como comprometida; investiga antes de confiar en sus entradas.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** crítica · **Categoría:** integridad · **Relevante para la seguridad:** sí

<a name="permission-denied"></a>

### permission_denied

**Permiso denegado**

- **Qué pasó:** Esa acción no estuvo permitida, así que no se cambió nada.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Comprueba la política de permisos o los ajustes de aprobación que rigen esta acción.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** media · **Categoría:** política · **Relevante para la seguridad:** sí

<a name="provider-exhausted"></a>

### provider_exhausted

**Límite del proveedor alcanzado**

- **Qué pasó:** Se alcanzó el límite de uso del proveedor del modelo. Puedes probar más tarde.
- **Qué puede haber pasado ya:** Una solicitud llegó a un servicio externo; no se realizó ningún pago
- **Qué hacer a continuación:** Comprueba cuota, límites de tasa y facturación del proveedor configurado.
- **Volver a intentarlo:** Reinténtalo más tarde: lo que lo impidió desaparece con el tiempo.
- **Gravedad:** media · **Categoría:** disponibilidad

<a name="provider-unavailable"></a>

### provider_unavailable

**Proveedor inaccesible**

- **Qué pasó:** No se pudo contactar al proveedor del modelo. Prueba dentro de un momento.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Comprueba el estado del proveedor, la alcanzabilidad de red y los puntos finales configurados.
- **Volver a intentarlo:** Reinténtalo más tarde: lo que lo impidió desaparece con el tiempo.
- **Gravedad:** media · **Categoría:** disponibilidad

<a name="timeout"></a>

### timeout

**Tiempo de espera agotado**

- **Qué pasó:** Esto tardó demasiado y se detuvo. Reintentarlo puede funcionar.
- **Qué puede haber pasado ya:** Resultado desconocido: comprueba el estado antes de reintentar
- **Qué hacer a continuación:** Si los tiempos de espera se repiten, comprueba la latencia de red y la respuesta del proveedor.
- **Volver a intentarlo:** Reintentarlo ahora puede funcionar.
- **Gravedad:** media · **Categoría:** disponibilidad

<a name="tool-unavailable"></a>

### tool_unavailable

**Herramienta no disponible**

- **Qué pasó:** Esa herramienta no está disponible ahora, así que esta parte no se hizo. Hay que activarla antes de reintentar.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Activa la herramienta para este modo en ajustes o elige otro enfoque.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** baja · **Categoría:** disponibilidad

<a name="unknown"></a>

### unknown

**Error inesperado**

- **Qué pasó:** Algo falló en este ordenador al procesarlo. Puedes intentarlo de nuevo.
- **Qué puede haber pasado ya:** Resultado desconocido: comprueba el estado antes de reintentar
- **Qué hacer a continuación:** Mira la cadena de causas redactadas del fallo en diagnósticos; una causa desconocida nunca se reintenta a ciegas.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** media · **Categoría:** interna

<a name="unsupported-claim"></a>

### unsupported_claim

**Afirmaciones sin respaldo retenidas**

- **Qué pasó:** Una o más afirmaciones no estaban respaldadas por las fuentes obtenidas y se retuvieron.
- **Qué puede haber pasado ya:** Se produjo un registro o un cambio local
- **Qué hacer a continuación:** No hace falta nada: es la puerta de fundamentación funcionando. Si es frecuente, comprueba la calidad de búsqueda.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** media · **Categoría:** integridad

<a name="wallet-acknowledgement-required"></a>

### wallet_acknowledgement_required

**Confirmación requerida**

- **Qué pasó:** Este paso necesita primero tu confirmación, así que no se firmó ni se envió nada. Lee lo que el monedero dice que hace esta acción, confírmalo exactamente y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Cuando una aprobación concedería derechos de gasto permanentes, cambiaría la propiedad o ejecutaría una actualización, o el monedero encuentra una discrepancia o no puede leer la solicitud, te pide que escribas primero su texto de confirmación exacto; nada genérico vale en su lugar.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** baja · **Categoría:** política

<a name="wallet-amount-invalid"></a>

### wallet_amount_invalid

**Importe no exacto para esta moneda**

- **Qué pasó:** Ese importe no es un importe exacto que esta moneda pueda contener, así que no se propuso nada. Comprueba el importe y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Envía un monto decimal simple, no más preciso de lo que la moneda permite y dentro del límite por transferencia del piloto.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** baja · **Categoría:** política

<a name="wallet-approval-rejected"></a>

### wallet_approval_rejected

**Aprobación no aceptada**

- **Qué pasó:** La aprobación de este pago no fue aceptada, así que no se firmó ni envió nada.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Aprueba los parámetros exactos con el PIN o la biometría del dueño; las aprobaciones no se transfieren entre parámetros.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** media · **Categoría:** política · **Relevante para la seguridad:** sí

<a name="wallet-backup-unavailable"></a>

### wallet_backup_unavailable

**Copia de respaldo ya mostrada una vez**

- **Qué pasó:** La clave de respaldo de este monedero ya se mostró una vez, así que no puede volver a mostrarse. Si no la guardaste, cancela la configuración y no envíes fondos a esta dirección.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Una copia del Piloto Cripto se revela exactamente una vez durante la configuración; no hay segunda revelación ni exportación.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** alta · **Categoría:** seguridad · **Relevante para la seguridad:** sí

<a name="wallet-broadcast-failed"></a>

### wallet_broadcast_failed

**No se pudo difundir el pago**

- **Qué pasó:** No se pudo difundir el pago. Comprueba su estado antes de reintentar.
- **Qué puede haber pasado ya:** Resultado desconocido: comprueba el estado antes de reintentar
- **Qué hacer a continuación:** Comprueba el endpoint RPC de la red y el recibo de la propuesta; el gasto no quedó registrado como enviado.
- **Volver a intentarlo:** Reinténtalo más tarde: lo que lo impidió desaparece con el tiempo.
- **Gravedad:** media · **Categoría:** disponibilidad

<a name="wallet-caller-refused"></a>

### wallet_caller_refused

**No se inició desde la propia ventana de VOOL**

- **Qué pasó:** Esta acción del monedero solo puede iniciarse desde la propia ventana de VOOL en este ordenador, así que no se cambió, firmó ni envió nada. Abre VOOL y reintenta allí.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Maneja las puertas de cartera confiables desde la página servida o la ventana nativa; una página de otro origen, un marco o un plugin se rechaza.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** alta · **Categoría:** seguridad · **Relevante para la seguridad:** sí

<a name="wallet-card-data-refused"></a>

### wallet_card_data_refused

**Datos de tarjeta rechazados**

- **Qué pasó:** Los números de tarjeta y códigos de seguridad nunca se guardan aquí; solo se acepta un token del proveedor. No se guardó nada.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Tokeniza la tarjeta con el proveedor y registra la referencia del token.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** alta · **Categoría:** seguridad · **Relevante para la seguridad:** sí

<a name="wallet-chain-identity-mismatch"></a>

### wallet_chain_identity_mismatch

**Identidad del punto de la cadena no probada**

- **Qué pasó:** El punto de la cadena no probó la identidad de la red que dice ser, así que no se firmó ni envió nada.
- **Qué puede haber pasado ya:** Una solicitud llegó a un servicio externo; no se realizó ningún pago
- **Qué hacer a continuación:** Comprueba el punto RPC de la red; un chain id o genesis mentiroso se rechaza antes de cualquier pago.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** alta · **Categoría:** seguridad · **Relevante para la seguridad:** sí

<a name="wallet-confirmation-required"></a>

### wallet_confirmation_required

**Confirmación escrita requerida**

- **Qué pasó:** Ese paso necesita primero la confirmación escrita, así que no se cambió nada. Confírmalo y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Lee la advertencia y escribe la frase de confirmación exacta para continuar.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** baja · **Categoría:** política

<a name="wallet-credential-mismatch"></a>

### wallet_credential_mismatch

**Las entradas no coincidían**

- **Qué pasó:** Las dos entradas no coincidían, así que no se creó nada. Introduce dos veces el mismo PIN o contraseña y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Una credencial del Piloto Cripto se confirma antes de que exista cualquier material de clave.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** baja · **Categoría:** política

<a name="wallet-dependency-unavailable"></a>

### wallet_dependency_unavailable

**Bibliotecas EVM no instaladas**

- **Qué pasó:** Esta compilación no puede firmar EVM ni codificar ABI: las bibliotecas de Ethereum no están instaladas en esta máquina. No se firmó ni envió nada. Solana, la custodia y los techos de gasto no se ven afectados; instala las bibliotecas y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Instala los extras EVM (eth-abi, eth-utils, eth-account) para habilitar los carriles EVM; todo lo demás de la cartera funciona sin ellos.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** media · **Categoría:** política

<a name="wallet-device-auth-denied"></a>

### wallet_device_auth_denied

**Desbloqueo con el dispositivo rechazado**

- **Qué pasó:** Touch ID o la contraseña de tu Mac se rechazó o no abrió este monedero, así que no se creó, cambió, desbloqueó, reveló, firmó ni envió nada. Reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Reintenta y confirma la solicitud de Touch ID o de la contraseña del Mac que muestra VOOL. Un monedero que aprueba con Touch ID no tiene PIN ni contraseña propios que usar en su lugar. Si la solicitud sigue fallando, restaura el monedero desde su frase de recuperación (un Crypto Pilot, desde la copia de respaldo mostrada en la configuración) y dale un PIN o una contraseña; una exportación de la clave privada sigue necesitando Touch ID o la contraseña del Mac.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** baja · **Categoría:** política

<a name="wallet-device-auth-unavailable"></a>

### wallet_device_auth_unavailable

**Desbloqueo con el dispositivo no disponible**

- **Qué pasó:** Touch ID o la contraseña de tu Mac no está disponible aquí para este monedero, así que no se creó, cambió, desbloqueó, reveló, firmó ni envió nada. Reintenta cuando esté disponible.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Un monedero nuevo puede aprobar con su propio PIN o contraseña en su lugar. Un monedero que aprueba con Touch ID no tiene PIN ni contraseña propios: si Touch ID y la contraseña del Mac siguen sin estar disponibles, restáuralo desde su frase de recuperación (un Crypto Pilot, desde la copia de respaldo mostrada en la configuración) y dale un PIN o una contraseña. Una exportación de la clave privada solo se abre con Touch ID o la contraseña del Mac, así que restaura el monedero en un Mac donde funcionen y exporta allí.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** media · **Categoría:** disponibilidad

<a name="wallet-disabled"></a>

### wallet_disabled

**El monedero está apagado**

- **Qué pasó:** El monedero está apagado, así que no se propuso ni envió ningún pago. Enciende el interruptor «Activar Cripto» en Ajustes, Cripto y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Enciende «Activar Cripto» en Ajustes, Cripto solo si quieres pagos en esta instalación. Si la variable VOOL_WALLET_ENABLED está definida, enciende o apaga el monedero diga lo que diga el interruptor.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** baja · **Categoría:** política

<a name="wallet-duplicate-payment"></a>

### wallet_duplicate_payment

**Pago ya propuesto o enviado**

- **Qué pasó:** Este pago ya estaba propuesto o enviado, así que no se envió otra vez.
- **Qué puede haber pasado ya:** Se produjo un registro o un cambio local
- **Qué hacer a continuación:** Comprueba el recibo de la propuesta existente; usa una clave de idempotencia nueva para un pago genuinamente nuevo.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** media · **Categoría:** integridad · **Relevante para la seguridad:** sí

<a name="wallet-environment-inactive"></a>

### wallet_environment_inactive

**Entorno de redes incorrecto**

- **Qué pasó:** Esa red pertenece al otro entorno de redes (Mainnet o redes de prueba), así que no se creó, propuso ni firmó nada. Cambia en Ajustes, Cripto, Opciones de desarrollador, y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Elige el entorno de red deliberadamente en los ajustes de Cripto; cuentas, saldos y recibos nunca cruzan entornos.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** media · **Categoría:** seguridad · **Relevante para la seguridad:** sí

<a name="wallet-export-pin-not-accepted"></a>

### wallet_export_pin_not_accepted

**La exportación solo se desbloquea con la autenticación del dispositivo**

- **Qué pasó:** La exportación nunca se desbloquea con un PIN, una contraseña o una frase de paso, solo con la autenticación del dispositivo (Touch ID o la contraseña de tu Mac), así que no se reveló nada. Reintenta sin el PIN ni la contraseña.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** La exportación se desbloquea solo con la autenticación del dispositivo, a propósito; un PIN, una contraseña o una frase de paso en la solicitud se rechaza antes de comprobarse siquiera.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** alta · **Categoría:** seguridad · **Relevante para la seguridad:** sí

<a name="wallet-export-refused"></a>

### wallet_export_refused

**Antigua forma de exportar retirada**

- **Qué pasó:** Esa forma de exportar una clave privada está retirada, así que no se reveló nada. Un monedero que permite exportar tiene el botón «Export private key» en Ajustes, Cripto, que solo se abre con Touch ID o la contraseña de tu Mac.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Usa «Export private key» en Ajustes, Cripto, Otras cuentas: solo el bolsillo heredado se exporta, y solo con Touch ID o la contraseña del Mac. Un monedero Crypto Pilot nunca se exporta; se recupera con la copia de respaldo mostrada en la configuración.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** alta · **Categoría:** seguridad · **Relevante para la seguridad:** sí

<a name="wallet-export-unavailable"></a>

### wallet_export_unavailable

**Exportación no disponible para este monedero**

- **Qué pasó:** Este monedero no se puede exportar así: es de solo lectura o no guarda ninguna clave en este equipo, su única copia de respaldo se mostró en la configuración y nunca se exporta, VOOL no exporta a la app de monedero que elegiste, o es un bolsillo heredado de una versión anterior que debe restaurarse desde su frase de recuperación antes de exportarse a MetaMask. No se reveló nada.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Solo se exporta un bolsillo heredado sellado en este equipo, hacia Phantom o MetaMask, y solo con Touch ID o la contraseña del Mac. Un monedero Crypto Pilot se recupera desde la copia de respaldo mostrada en la configuración. Un bolsillo heredado de una versión anterior se exporta a MetaMask después de restaurarlo desde su frase de recuperación.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** alta · **Categoría:** seguridad · **Relevante para la seguridad:** sí

<a name="wallet-insufficient-funds"></a>

### wallet_insufficient_funds

**El saldo no cubre el importe y la comisión**

- **Qué pasó:** El saldo no cubre el importe más lo máximo que puede costar la comisión de esta red, así que no se preparó nada. Baja el importe o añade fondos y reintenta.
- **Qué puede haber pasado ya:** Una solicitud llegó a un servicio externo; no se realizó ningún pago
- **Qué hacer a continuación:** Lo gastable excluye otras retenciones en curso; un remanente de Solana debe quedar sin ceros finales y cubrir al menos la renta.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** baja · **Categoría:** política

<a name="wallet-legacy-surface-retired"></a>

### wallet_legacy_surface_retired

**Camino de dinero heredado retirado**

- **Qué pasó:** Ese camino de dinero está retirado: todo pago pasa por el flujo de propuesta y aprobación del monedero. No se firmó ni envió nada.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Usa las herramientas de cartera (wallet.propose) o la API de cartera; el firmante, gastador y exportador heredados ya no sirven.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** alta · **Categoría:** seguridad · **Relevante para la seguridad:** sí

<a name="wallet-limit-exceeded"></a>

### wallet_limit_exceeded

**Límite de gasto superado**

- **Qué pasó:** Ese pago supera un límite de gasto, así que no se envió.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Revisa los límites por transacción, diarios y por destino antes de reintentar.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** media · **Categoría:** política · **Relevante para la seguridad:** sí

<a name="wallet-network-disabled"></a>

### wallet_network_disabled

**Red no declarada para este monedero**

- **Qué pasó:** Esa red no es de las que este monedero declara para esta acción, así que no se creó ni envió nada.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Usa una de las filas de red declaradas; el registro rechaza cadenas no declaradas, alias y nombres parecidos.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** alta · **Categoría:** seguridad · **Relevante para la seguridad:** sí

<a name="wallet-not-found"></a>

### wallet_not_found

**Monedero, propuesta o destino no reconocido**

- **Qué pasó:** Ese monedero, propuesta o destino no se reconoció, así que no se hizo nada. Compruébalo y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Comprueba el id de cartera, el id de propuesta o la dirección destino y vuelve a intentarlo.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** baja · **Categoría:** política

<a name="wallet-outbound-refused"></a>

### wallet_outbound_refused

**Solicitud fuera de los destinos aprobados**

- **Qué pasó:** Esa solicitud no pudo salir de los destinos aprobados del monedero, así que no se estableció conexión.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Comprueba los orígenes RPC declarados y la política de recursos de pago; hosts privados, de metadatos y no aprobados se rechazan.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** alta · **Categoría:** seguridad · **Relevante para la seguridad:** sí

<a name="wallet-password-invalid"></a>

### wallet_password_invalid

**Contraseña del monedero no válida**

- **Qué pasó:** La contraseña no cumplió la política o no desbloqueó el monedero, así que no se firmó ni se cambió nada. Comprueba la contraseña y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Las contraseñas del monedero tienen de 10 a 128 caracteres y no solo dígitos. Una contraseña errónea deja la clave sellada y cuenta para el bloqueo del monedero tras demasiados intentos fallidos.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** baja · **Categoría:** política

<a name="wallet-paykit-unavailable"></a>

### wallet_paykit_unavailable

**Extra pay de Solana no instalado**

- **Qué pasó:** Esta compilación no puede pagar ofertas x402 o MPP de Solana: el extra opcional pay (Solana pay-kit, Python 3.11 o posterior) no está instalado en esta máquina. No se firmó ni envió nada. Todo lo demás de la cartera funciona sin él; instálalo y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Instala el extra opcional pay de VOOL en Python 3.11 o posterior para habilitar los pagos con pay-kit; la cartera conserva su propio firmante, sus techos de gasto y su aprobación en cualquier caso.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** media · **Categoría:** política

<a name="wallet-payment-channel-off"></a>

### wallet_payment_channel_off

**Canal de pago desactivado**

- **Qué pasó:** Ese pago pasa por un canal de pago que está desactivado, así que no se firmó ni se envió nada. Activa el canal en Ajustes, Cripto, Canales de pago, y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Activa un canal de pago en los ajustes de Cripto solo si quieres que los pagos pasen por él; el tope y tu aprobación se aplican en todos los canales.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** baja · **Categoría:** política

<a name="wallet-pin-invalid"></a>

### wallet_pin_invalid

**PIN no válido**

- **Qué pasó:** El PIN no cumplió la política o no abrió el monedero, así que no se firmó nada. Comprueba el PIN y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Usa un PIN de 6 a 12 dígitos; un PIN erróneo deja la clave sellada intacta.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** baja · **Categoría:** política

<a name="wallet-quote-expired"></a>

### wallet_quote_expired

**La vista previa caducó o fue sustituida**

- **Qué pasó:** Esta vista previa de transferencia caducó o fue sustituida, así que no puede aprobarse y no se envió nada. Refresca la vista previa y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Una cotización vive poco y queda sustituida por un refresco o un cambio de entorno; la aprobación siempre liga la actual.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** baja · **Categoría:** política

<a name="wallet-quote-mismatch"></a>

### wallet_quote_mismatch

**La aprobación ya no coincide con la vista previa**

- **Qué pasó:** La aprobación ya no coincide con la transferencia vista, así que no se firmó ni envió nada. Empieza de nuevo con una vista previa fresca.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Una aprobación liga el resumen de una cotización; cambiar cuenta, cadena, destinatario, monto, entorno o comisión exige aprobación nueva.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** alta · **Categoría:** seguridad · **Relevante para la seguridad:** sí

<a name="wallet-quote-unavailable"></a>

### wallet_quote_unavailable

**Saldo o comisión desconocidos**

- **Qué pasó:** La red no dio una respuesta completa sobre saldo o comisión de esta transferencia, así que no se preparó aprobación ni se envió nada. Prueba dentro de un momento.
- **Qué puede haber pasado ya:** Una solicitud llegó a un servicio externo; no se realizó ningún pago
- **Qué hacer a continuación:** Los datos desconocidos de cadena nunca se tratan como cero; comprueba el punto final de la fila y su prueba de identidad.
- **Volver a intentarlo:** Reinténtalo más tarde: lo que lo impidió desaparece con el tiempo.
- **Gravedad:** media · **Categoría:** disponibilidad

<a name="wallet-recipient-refused"></a>

### wallet_recipient_refused

**El destinatario no puede recibir esto con seguridad**

- **Qué pasó:** Ese destinatario no puede recibir con seguridad esta transferencia en esta red, así que no se preparó nada. Comprueba la dirección y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Rechazado: una cuenta nueva de Solana bajo la renta mínima, una cuenta ejecutable o fuera de curva, una suma de comprobación EIP-55 incorrecta, la dirección cero, los precompilados y los contratos del sistema.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** media · **Categoría:** política

<a name="wallet-recovery-refused"></a>

### wallet_recovery_refused

**La prueba de recuperación no encaja con este monedero**

- **Qué pasó:** Esa prueba de recuperación no encaja con este monedero, así que no se cambió nada: la clave guardada debe ser la copia que VOOL mostró exactamente para esta dirección, en el formato mostrado. Compruébalo y reintenta, o usa las otras opciones.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** La recuperación resella solo una cartera existente y solo desde una copia que decodifique a la clave propia de esa cartera (ambas direcciones).
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** alta · **Categoría:** seguridad · **Relevante para la seguridad:** sí

<a name="wallet-request-ambiguous"></a>

### wallet_request_ambiguous

**Encaja más de una cuenta o red**

- **Qué pasó:** Más de una cuenta o red encaja con esta transferencia, así que no se propuso nada. Di qué cuenta o red quieres y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Una transferencia nombra una cuenta en una red; los candidatos están listados en el contexto del fallo.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** baja · **Categoría:** política

<a name="wallet-send-needs-vool-wallet"></a>

### wallet_send_needs_vool_wallet

**Para enviar hace falta un VOOL Wallet**

- **Qué pasó:** Esta cuenta no puede enviar desde VOOL en esta red: aquí solo envía un VOOL Wallet creado en este dispositivo. Una cuenta conectada o de solo observación sigue pudiendo recibir y mostrar su saldo. No se firmó ni se envió nada. Para enviar, elige un VOOL Wallet de este dispositivo y vuelve a intentarlo.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Envía desde un VOOL Wallet de este dispositivo, o desde la propia app del monedero conectado; VOOL no firma nada en esta red para una cuenta conectada o de solo observación.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** baja · **Categoría:** política

<a name="wallet-setup-state-invalid"></a>

### wallet_setup_state_invalid

**El paso de configuración no se aplica ahora**

- **Qué pasó:** Ese paso de configuración no aplica ahora a este monedero, así que no cambió nada. Comprueba el estado de configuración del monedero y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** La configuración avanza: generando, esperando copia, copia revelada, lista; cancelar y reanudar nunca revelan una copia dos veces.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** baja · **Categoría:** política

<a name="wallet-signature-invalid"></a>

### wallet_signature_invalid

**La firma no coincidía con la clave del monedero**

- **Qué pasó:** La firma devuelta para este pago no coincidía con la clave del monedero, así que no se difundió.
- **Qué puede haber pasado ya:** Una solicitud llegó a un servicio externo; no se realizó ningún pago
- **Qué hacer a continuación:** Comprueba el firmante conectado; una firma que no coincide se rechaza antes de difundir.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** alta · **Categoría:** seguridad · **Relevante para la seguridad:** sí

<a name="wallet-signing-unavailable"></a>

### wallet_signing_unavailable

**El monedero no puede firmar desde aquí**

- **Qué pasó:** Este monedero no puede firmar desde aquí: es de solo observación o su firmante externo no está conectado. No se envió nada; conecta un firmante y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Conecta la cartera externa o crea una Cartera VOOL en esa red en Ajustes, Cripto.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** media · **Categoría:** política

<a name="wallet-simulation-failed"></a>

### wallet_simulation_failed

**El pago no pasó la simulación**

- **Qué pasó:** El pago no pasó la simulación, así que no se envió. Comprueba saldo y destino y reintenta.
- **Qué puede haber pasado ya:** Una solicitud llegó a un servicio externo; no se realizó ningún pago
- **Qué hacer a continuación:** Comprueba el saldo, el destino y el endpoint RPC de la red, y propón de nuevo.
- **Volver a intentarlo:** Reinténtalo más tarde: lo que lo impidió desaparece con el tiempo.
- **Gravedad:** media · **Categoría:** disponibilidad

<a name="wallet-storage-class-refused"></a>

### wallet_storage_class_refused

**Almacenamiento de claves solo en memoria**

- **Qué pasó:** Esta instalación guarda la clave del dispositivo solo en memoria, así que un monedero creado aquí no podría abrirse tras reiniciar. No se creó nada. Cambia el almacenamiento de claves a un modo persistente y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Las carteras del Piloto Cripto se rechazan mientras VOOL_KEY_STORAGE_MODE sea ephemeral; usa almacenamiento file o keyring.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** media · **Categoría:** política

<a name="wallet-unlock-throttled"></a>

### wallet_unlock_throttled

**Monedero bloqueado tras desbloqueos fallidos**

- **Qué pasó:** Hubo demasiados intentos erróneos de PIN o contraseña, así que este monedero queda bloqueado un rato y no se desbloqueó nada. Espera y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Los desbloqueos fallidos se cuentan de forma durable por cartera y para toda la app, entre reinicios; el bloqueo se levanta solo.
- **Volver a intentarlo:** Reinténtalo más tarde: lo que lo impidió desaparece con el tiempo.
- **Gravedad:** alta · **Categoría:** seguridad · **Relevante para la seguridad:** sí

<a name="wallet-x402-cap-exceeded"></a>

### wallet_x402_cap_exceeded

**Recurso de pago por encima del tope automático**

- **Qué pasó:** Un recurso de pago pidió más del tope de pago automático, así que no se pagó.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Sube VOOL_WALLET_X402_CAP_MINOR deliberadamente o paga el recurso con una propuesta explícita.
- **Volver a intentarlo:** No vuelvas a enviar la misma solicitud sin cambios.
- **Gravedad:** media · **Categoría:** política · **Relevante para la seguridad:** sí

<a name="wallet-x402-sdk-unavailable"></a>

### wallet_x402_sdk_unavailable

**Extra x402 no instalado**

- **Qué pasó:** Esta compilación no puede pagar ofertas x402 en Base mediante el x402 Foundation SDK: el extra opcional x402 no está instalado en esta máquina. No se firmó ni envió nada. Todo lo demás de la cartera funciona sin él; instálalo y reintenta.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Instala el extra opcional x402 de VOOL para pagar ofertas x402 en Base desde una cartera Crypto Pilot; la cartera conserva su propio firmante, sus techos de gasto y su aprobación en cualquier caso.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** media · **Categoría:** política

<a name="x402-scheme-unavailable"></a>

### x402_scheme_unavailable

**Esquema de pago no admitido**

- **Qué pasó:** Esta oferta de pago necesita un esquema que este monedero no puede probar de punta a punta, así que no se firmó nada. Elige una oferta cuyo esquema, token y facilitador estén verificados para esta red.
- **Qué puede haber pasado ya:** No se envió, firmó ni cambió nada
- **Qué hacer a continuación:** Usa una oferta cuyo esquema, token y facilitador estén verificados para la red; el soporte parcial de firma no alcanza aquí.
- **Volver a intentarlo:** Reinténtalo después de hacer el cambio que indica esta entrada.
- **Gravedad:** baja · **Categoría:** política
