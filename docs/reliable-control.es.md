<div lang="es" dir="ltr">

# Control fiable y transferencia de reproducción

<!-- language-navigation:start -->
<p dir="ltr">
<a href="reliable-control.md" lang="en" dir="ltr">English</a> · <a href="reliable-control.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="reliable-control.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="reliable-control.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="reliable-control.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="reliable-control.es.md" lang="es" dir="ltr">Español</a> · <a href="reliable-control.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="reliable-control.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="reliable-control.he.md" lang="he" dir="rtl">עברית</a> · <a href="reliable-control.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="reliable-control.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="reliable-control.it.md" lang="it" dir="ltr">Italiano</a> · <a href="reliable-control.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="reliable-control.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="reliable-control.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="reliable-control.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="reliable-control.pt.md" lang="pt" dir="ltr">Português</a> · <a href="reliable-control.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="reliable-control.th.md" lang="th" dir="ltr">ไทย</a> · <a href="reliable-control.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="reliable-control.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="reliable-control.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="reliable-control.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="reliable-control.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Índice de documentación](wiki/Home.md)

Estas funciones requieren el controlador y la CLI de una revisión que contenga esta guía. La comparación exacta de ajustes y la transferencia también requieren un reproductor actualizado. Los archivos de versiones antiguas y la revisión histórica de la CLI de la guía de instalación no adquieren estas funciones automáticamente. Instale **todos los archivos `cli/*.py` juntos**, incluido `preset_session.py`; conserve la configuración privada.

![Flujo de control, punto de control y transferencia](assets/reliable-control.svg)

## Activar resultados persistentes con límites

Añada este bloque opcional a la configuración privada del servidor; después valídela y reinicie el servidor. Elija una ruta absoluta en un sistema de archivos local persistente donde la cuenta del servicio pueda escribir. En Unix, el directorio debe ser privado (0700); los archivos existentes de diario y bloqueo deben ser archivos normales privados (0600). En Windows, restrinja la ACL del directorio a la cuenta del servicio. Un único proceso es propietario de cada diario.

```json
"journal": {
  "path": "/var/lib/ottplay-control/private/operations.json",
  "max_records": 1024,
  "retention_seconds": 86400
}
```

El servidor crea los directorios que falten con modo 0700. Los límites son 1–4096 registros, 60–86400 segundos y 4 MiB en disco. La retención debe cubrir `command_ttl_seconds`. Un diario lleno rechaza nuevas operaciones con clave en vez de expulsar registros vigentes. Los archivos dañados, un directorio Unix inseguro, otro escritor o un fallo de escritura persistente provocan un rechazo seguro. La ruta de comandos existente en memoria sigue siendo la opción predeterminada.

La unidad systemd incluida crea `/var/lib/ottplay-control` con modo 0700. Para Compose, prepare un directorio del host propiedad del UID 65532, establezca `CONTROL_SERVER_STATE` en su ruta absoluta y combine `deploy/compose.yml` con `deploy/compose.journal.yml`. Este último solo añade el montaje de estado escribible; el sistema de archivos raíz sigue siendo de solo lectura. Para Helm, prepare por separado una solicitud de volumen persistente y configure `journalExistingClaim=CLAIM_NAME`. Se monta en `/var/lib/ottplay-control`; use el subdirectorio privado mostrado antes. El chart mantiene una réplica, `Recreate` y `fsGroupChangePolicy: OnRootMismatch` para que un nuevo montaje no amplíe recursivamente los permisos de directorios privados ya creados. Compruebe que el controlador de almacenamiento elegido admite esa semántica de propiedad. El chart no crea ni elimina la solicitud, y activar el montaje no activa el diario: eso sigue dependiendo de la configuración privada del servidor.

Para `POST /api/requests`, añada `idempotency_key` junto a `action` y `params`. Los POST de comandos antiguos aceptan `Idempotency-Key` en su lugar. Las claves contienen 16–80 letras ASCII, dígitos, guiones bajos o guiones; genere una clave aleatoria nueva para cada intención. El mismo propietario/clave con el mismo dispositivo, acción y carga JSON normalizada devuelve el ID original sin volver a encolar. Cambiar la carga devuelve 409. El ámbito de las claves es el ID del operador (o el administrador), no el dispositivo. Tras vencer la retención, la clave puede aceptarse como una intención nueva: los clientes no deben reintentar una intención antigua fuera de ese plazo.

`GET /api/operations?device_id=DEVICE` informa de si el diario está activo, sus límites y el TTL de los comandos. Añada `&key=KEY` para leer una operación:

- `pending`: aceptada; aún no hay respuesta conservada.
- `responded`: ha llegado una respuesta validada del reproductor; `result_status` es `ok`, `rejected` o `unsupported`. El cuerpo del resultado original sigue caducando normalmente.
- `acknowledged`: un consumidor antiguo confirmó el comando. Esto no demuestra reproducción visible ni que se guardara un cambio de ajustes.
- `unknown`: venció el plazo o el servidor se reinició antes de conservar una respuesta. El servidor **nunca vuelve a ejecutar** este comando tras reiniciarse.

Un POST repetido con clave devuelve `status: retained`, el ID original y el estado. Los POST nuevos ordinarios mantienen `status: queued`. Los registros del diario contienen solo propietario, dispositivo, acción, ID aleatorios, un resumen criptográfico de la carga, estado y plazos. No contienen cargas de comandos, URL de proveedores, tokens, capturas ni cuerpos de respuesta. Los comandos pendientes no se guardan de forma persistente. Esto es deduplicación y seguimiento de resultados limitados, no ejecución exactamente una vez, una cola de trabajo persistente ni alta disponibilidad entre varios servidores.

## Planificar, aplicar, reanudar y verificar Local/Home

Se conserva el comportamiento existente de `ott PLAYER load PRESET`. Para el nuevo flujo:

```sh
ott tv load Local plan
ott tv load Local apply
ott tv load Local resume
ott tv load Local verify
```

`plan` funciona sin conexión: valida el preajuste local y enumera las posiciones de ajustes y el perfil activo deseado sin mostrar su contenido. No inspecciona el reproductor. `verify` hace comparaciones exactas de solo lectura y comprueba el perfil M3U seleccionado. Devuelve un código distinto de cero si algo difiere o la comparación no está admitida.

`apply` requiere el diario del servidor. Crea un punto de control privado y compara todos los proveedores configurados antes de la primera modificación. Cada guardado compara los bytes almacenados con los valores esperados mediante `settings_compare`; si coinciden, omite la escritura. La verificación final abarca Plex, todas las posiciones M3U, las posiciones Stalker opcionales y el perfil M3U seleccionado. Las comparaciones devuelven un booleano, nunca secretos almacenados.

El punto de control predeterminado está bajo `~/.config/ottplay-control/checkpoints/`. Use `--checkpoint /absolute/private/path/session.json` después del modo para elegir otro archivo. Los puntos de control contienen el dispositivo, el nombre del preajuste, el hash de la revisión del servidor/preajuste, los nombres de pasos terminados y una clave aleatoria de operación pendiente con su plazo. No contienen valores de ajustes. Los archivos se sustituyen atómicamente después de fsync y se bloquean frente a escritores CLI concurrentes. Mantenga disponible el preajuste local correspondiente. En Unix, el directorio debe ser privado (0700), con archivos normales en 0600. En Windows, restrinja la ACL heredada del directorio al usuario que ejecuta la CLI; los bits de modo Unix no imponen permisos de acceso en Windows.

`resume` valida el mismo servidor, dispositivo y revisión del preajuste y después lee el resultado pendiente. Una respuesta conservada permite volver a comprobar inmediatamente. Un resultado incierto impide enviar modificaciones hasta que hayan transcurrido el plazo del comando anterior, el tiempo de espera de la solicitud y un pequeño margen. Ejecute `resume` otra vez pasado ese intervalo. Vuelve a comprobar los ajustes almacenados incluso para pasos terminados, para no confundir cambios del usuario con escrituras previas correctas. Nunca repite a ciegas un comando relativo. La selección absoluta de proveedor/perfil puede repetirse después del período de protección.

Un `apply` nuevo nunca sobrescribe un punto de control existente. Reutilícelo con `resume` o elija un archivo nuevo para un preajuste modificado. Los dispositivos distintos tienen puntos de control separados. No hay una transacción ni reversión que abarque todos los reproductores; las escrituras previas correctas permanecen si falla un paso posterior. `verified` confirma los valores almacenados del reproductor en el momento de la comparación, no una carga de red correcta ni salida física de vídeo/audio.

## Dar a las integraciones solo la autoridad necesaria

Use [operadores de comandos ordinarios](security-design.md#scoped-command-operators) para HA, un puente RF o un agente, en lugar de distribuir `admin_token`. Permita ID exactos de dispositivos y operaciones como `input.channel_up` y `input.channel_down`. Las operaciones de ajustes requieren permisos explícitos `settings_compare`, `provider`, `profiles`, `status`, `profile`, `profile_settings` y `provider_settings` para este flujo de preajustes. La transferencia necesita `playback_handoff_export` en el origen y `playback_handoff_import` en el destino. Una entrada de operador combina los dispositivos y acciones enumerados; use identidades distintas cuando una misma operación no deba aplicarse a todos esos dispositivos. Los tokens y el consentimiento para diagnósticos siguen siendo autorizaciones separadas.

Los operadores no pueden consultar colas de reproductores, enviar respuestas de reproductor, enumerar todos los dispositivos, leer salida de depuración administrativa ni resultados de otros operadores. Restringir el ámbito de una acción también impide leer su operación conservada. Eliminar un operador y reiniciar revoca su credencial. No reutilice un ID de operador para otro actor mientras permanezcan sus antiguos registros del diario.

## Continuar en otro reproductor

```sh
ott handoff living-room bedroom
```

El origen exporta una referencia de directo/archivo de versión 1: proveedor, identidad opaca del origen, huella opaca del elemento `channel-v1:<8 hex>.<8 hex>` y, para archivo, la hora actual del programa. Los ID de proveedor sin procesar y las URL de las emisiones permanecen locales. El destino debe tener **el mismo origen/cuenta configurado**, resolver un único elemento coincidente en su catálogo y obtener una URL nueva con sus propias credenciales locales. El controlador no copia una URL reproducible ni modifica las credenciales del destino. El origen sigue reproduciendo. Un resultado correcto de la CLI significa que el destino envió la solicitud, no que se hayan verificado su pantalla o altavoces físicos.

La versión 1 admite catálogos de directo/archivo `m3u`, `xtream` y `stalker` cuando el reproductor puede proporcionar las identidades requeridas. Los elementos desconocidos o ambiguos, los orígenes incompatibles y los rechazos por políticas locales fallan sin recurrir al nombre como alternativa. VOD/series, correspondencias entre cuentas distintas, pausar el origen y sincronizar continuamente historial/favoritos no forman parte de este primer contrato.

## Ejercicios de validación y recuperación

Ejecute `go test -race ./...`, `go vet ./...` y `python3 -m unittest discover -s tests`. Las pruebas cubren respuestas perdidas tras escribir correctamente, comparación exacta antes de reanudar, desviación de configuración, plazos de operaciones desconocidas, dos escritores, diarios dañados, lecturas entre operadores y cargas de transferencia mal formadas. En un reproductor de preproducción, interrumpa además la CLI tras guardar ajustes, reinicie el controlador, reanude y verifique; pruebe una transferencia entre orígenes coincidentes y no coincidentes. Confirme el vídeo/audio por separado.

Para la validación local entre repositorios, con la copia de trabajo actual de FOSS y sus dependencias de desarrollo instaladas, ejecute:

```sh
node scripts/smoke_foss_roadmap.cjs /path/to/ottplay-foss
```

La prueba de humo construye este servidor Go, compila todo el proyecto TypeScript de FOSS y ejecuta su transporte real, manejadores de comandos, identidad del origen y códec de listas incluido frente a la CLI Python. Comprueba ajustes exactos para los tres proveedores admitidos, discrepancias, transferencia en directo con ID locales de filas distintos, desplazamientos de búsqueda en archivo, fallos al resolver URL y validación estricta del formato transmitido. Solo el almacenamiento, las políticas del proveedor y las interfaces del host de reproducción son componentes simulados de prueba; no verifica un navegador, salida física multimedia ni acceso real a cuentas. Se enlaza a loopback, usa archivos temporales privados, no contacta ninguna URL de emisión y elimina sus archivos/servidor al terminar.

</div>
