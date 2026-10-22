<div lang="es" dir="ltr">

# OTA de Android: reproductores normales y agente root independiente

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-ota.md" lang="en" dir="ltr">English</a> · <a href="android-ota.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-ota.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-ota.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-ota.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-ota.es.md" lang="es" dir="ltr">Español</a> · <a href="android-ota.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-ota.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-ota.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-ota.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-ota.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-ota.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-ota.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-ota.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-ota.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-ota.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-ota.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-ota.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-ota.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-ota.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-ota.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-ota.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-ota.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-ota.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Para **k8a/fire8 y otros reproductores Capacitor**, use el actualizador Wi-Fi del reproductor normal, no el comando del agente root:

```sh
ott k8a update --variant api22
ott k8a update --version v1.1.58-beta.3 --variant api22
ott k8a update --variant api22 --prepare-only
ott k8a update status
```

Omitir `--version` selecciona la última beta publicada. Use `full` en vez de `api22` en Android 7+. La CLI localiza la versión oficial, comprueba su manifiesto, prepara el APK y consulta su estado antes de solicitar la instalación una sola vez. Android puede exigir confirmación en la tableta. Desactive el quiosco y desbloquee expresamente los ajustes protegidos antes del mantenimiento; el comando nunca cambia esas políticas. Consulte el [procedimiento completo de la CLI](cli.md#capacitor-apk-updates-over-wi-fi).

Se necesita un APK ya instalado compatible con `AppUpdate`, Wi-Fi y un reproductor en ejecución conectado al controlador. Las siguientes actualizaciones normales no requieren USB, pero no se puede alcanzar una tableta sin conexión o una WebView detenida. Una primera instalación, un actualizador no disponible, una aplicación dañada o perder la red aún pueden requerir recuperación local. Esto no implica un programador automático de versiones ni un instalador silencioso general.

El resto de esta guía describe un **agente de mantenimiento a1 distinto, con root**, limitado a la instalación Matricom/API19 preparada `play.ott.kitkat`. No está ya instalado ni se puede usar en dispositivos Fire/Capacitor normales.

Tras la preparación inicial por USB con root, el agente Android independiente puede recibir actualizaciones firmadas por su conexión HTTPS saliente. No necesita una WebView funcional para actualizarse. El agente 0.1.2 también admite un paquete coordinado de APK y ayudante táctil del sistema, con archivos de recuperación guardados y verificación antes de confirmar.

Esta guía explica todo el proceso. Para campos exactos del manifiesto, límites y requisitos de hardware, consulte la [referencia del agente nativo](../native/android-agent/README.md#signed-updates-over-wi-fi).

## Qué se actualiza

![Arquitectura OTA: los bytes revisados se firman, publican por HTTPS directo, verifican mediante el agente independiente y comprueban tras instalar. El despliegue web es separado.](images/ota-architecture.svg)

- **Agente nativo:** `kind: agent` sustituye su propio binario ARMv7. También funciona cuando OTT está desactivado y se usa otra aplicación HOME.
- **Solo APK:** `kind: apk` sustituye el contenedor conservando sus datos. OTT debe estar activado y en primer plano. El ayudante integrado del candidato debe coincidir exactamente con el ayudante instalado en el sistema.
- **APK y ayudante táctil:** `kind: bundle` actualiza ambos componentes como pareja. Un agente antiguo primero debe recibir una actualización de solo agente compatible con paquetes.
- **Reproductor alojado:** despliegue el artefacto web verificado en el origen existente; después recargue e inspeccione un reproductor activo. Actualizar el APK no despliega el JavaScript alojado. Consulte el [flujo de publicación](release-workflow.md).
- **Controlador y CLI:** despliegue el controlador e instale por separado todo el conjunto de módulos CLI. Actualizar uno no sustituye código en la tableta.

Firmware, kernel, WebView del sistema, intermediario root y scripts de arranque quedan fuera de este formato OTA. La [referencia nativa](../native/android-agent/README.md#bootstrap) describe los límites de preparación inicial y recuperación. Las operaciones OTA admitidas no requieren ADB por red ni una consola remota arbitraria.

## De una versión revisada a una instalación confirmada

1. **Seleccione bytes verificados.** Fusione cambios revisados cuando pase CI. Descargue el artefacto deseado y verifique su revisión de origen, pruebas inmutables de publicación y suma de comprobación. Extraiga el binario `agent` del archivo nativo; el archivo en sí no es una carga de agente instalable.
2. **Prepare y firme.** Use la clave Ed25519 privada existente cuya clave pública quedó fijada durante la preparación inicial. Mantenga esa clave y las credenciales del dispositivo privadas.
3. **Publique y compruebe el transporte.** Publique solo la carga y `signed.json` en rutas HTTPS inmutables. Ambos deben devolver sus bytes directamente con HTTP 200; el actualizador rechaza redirecciones. Ejecute `verify` en la herramienta de empaquetado.
4. **Envíe una actualización.** Registre el ID de solicitud y compruebe el estado tras la confirmación. Una respuesta perdida o incierta no justifica repetir una operación a ciegas.
5. **Confirme el efecto.** Compare hashes y versiones instalados recién obtenidos con la versión preparada. En un reproductor activo compruebe también destino del quiosco, cola y avance de la reproducción. La imagen y el sonido físicos requieren observación.

Ejemplo de una versión del agente; sustituya la versión seleccionada y las rutas reales:

```sh
python3 native/android-agent/prepare_update.py prepare \
  --kind agent --version 0.1.2-beta.4 \
  --payload /private/release/agent \
  --url https://updates.example/0.1.2-beta.4/agent \
  --manifest-url https://updates.example/0.1.2-beta.4/signed.json \
  --key /private/update.key --output /private/ota-0.1.2-beta.4

# Publish the prepared agent and signed.json, then verify the published bytes.
python3 native/android-agent/prepare_update.py verify \
  --directory /private/ota-0.1.2-beta.4

ott --receipt --timeout 40 a1 android update \
  https://updates.example/0.1.2-beta.4/signed.json ENVELOPE_SHA256
ott --timeout 30 a1 android status
ott a1 android operation REQUEST_ID
```

`ENVELOPE_SHA256` es el `envelope_sha256` de la herramienta de empaquetado, no el hash de la carga. `accepted` significa admisión, no instalación correcta. La sustitución del propio agente puede dejar un comprobante anterior como `unknown`; el nuevo entorno de ejecución, la versión esperada del agente y el hash instalado coincidente demuestran la instalación. La [referencia CLI](cli.md#independent-android-agent) describe el enrutamiento nativo independiente y la gestión de comprobantes.

## Cómo se recupera la transacción APK/ayudante

![Transacción APK/ayudante: comprobación previa, guardar pareja anterior, aplicar, verificar y confirmar; los fallos restauran la pareja anterior y la recuperación incompleta mantiene bloqueados los cambios de la aplicación.](images/ota-transaction.svg)

El paquete contiene exactamente `app.apk` y `ott-touch-guard`. La herramienta puede crearlo directamente a partir del APK contenedor firmado usando `--kind bundle --apk`. Extrae el ayudante integrado, de modo que las dos copias entregadas coincidan. Consulte el [comando y requisitos del paquete](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

Antes de cambiar archivos, el agente exige una pareja instalada coincidente, espacio libre suficiente, un reproductor OTT sensible en primer plano, partición de sistema de solo lectura y soporte anunciado de retroceso de APK. Para un quiosco bloqueado verifica el mismo ID de canal de TV o los ID VPortal ordenados; no basta con igual longitud de cola. Rechaza colas bloqueadas no compatibles. También rechaza una aplicación OTT desactivada intencionadamente, conservando la aplicación HOME actual del usuario.

Los archivos de recuperación y la transacción pendiente se guardan persistentemente antes de detener la aplicación. El agente espera a que se detengan la aplicación y el ayudante anterior, sustituye el ayudante atómicamente, devuelve `/system` a solo lectura, instala el APK y verifica el resultado. Un fallo al aplicar o verificar activa la restauración de la pareja anterior. La recuperación al arrancar restaura archivos sin iniciar OTT ni tomar HOME.

Si la recuperación no puede terminar, `updates.recovery_failed` es verdadero. Siguen disponibles los diagnósticos y la reparación firmada de solo agente; se bloquean los cambios de la aplicación y el watchdog de reproducción. No se debe borrar la transacción guardada para forzar una instalación. Recuperar binarios no revierte migraciones de datos de la aplicación y el firmware aún puede rechazar un retroceso. Consulte las [limitaciones de recuperación](../native/android-agent/README.md#coordinated-wrapper-and-helper-updates).

## Resultados y registro de aceptación

![Gráfico de validación: 488 pruebas CLI, 576 de herramientas de publicación y 6 del empaquetador OTA; la tableta de referencia ejecuta el nuevo agente verificado, mientras la aceptación física APK/ayudante sigue pendiente.](images/ota-validation.svg)

La auditoría del 14 de octubre de 2026 superó 488 pruebas CLI (una omitida), 576 de herramientas de publicación, seis del empaquetador OTA, comprobaciones nativas Go de carreras y estáticas, y una compilación ARMv7. Los números describen el tamaño de las suites, no cobertura ni rendimiento. Las pruebas con inyección de fallos ejercitan archivos inválidos, transacciones interrumpidas, restauración parcial, cambios de destino y bloqueo durante recuperación. La revisión independiente también detectó y corrigió una comprobación insuficiente de identidad de cola y validación APK/ayudante diferente entre host y dispositivo.

Según lo comprobado el 15 de octubre de 2026:

- La implementación OTA está fusionada en el [PR del controlador #104](https://github.com/open-ott-play/ottplay-control-server/pull/104) e incluida en [v0.1.2-beta.4](https://github.com/open-ott-play/ottplay-control-server/releases/tag/v0.1.2-beta.4).
- Los paquetes firmados actual y anterior del agente están publicados en el [host de distribución OTA](https://android-updates.ottplay.here.now/). Sus bytes de carga y sobre se descargaron y compararon correctamente por HTTPS directo.
- La tableta de referencia pasó de `0.1.1-beta.21` a `0.1.2-beta.4` por el canal OTA independiente. La versión del agente en ejecución y el SHA-256 instalado coincidían con la versión elegida; cambió su entorno de ejecución y el ID de arranque del sistema se mantuvo. OTT seguía sin un proceso de aplicación activo, y no se envió ninguna operación de inicio de aplicación ni selección HOME.
- El estado nativo informó del hash del ayudante táctil del sistema, ningún paquete pendiente y ningún fallo de recuperación. Los metadatos APK no estaban disponibles; esta observación no establece la versión ni el hash del APK instalado.
- La transacción coordinada APK/ayudante está validada en el host; la instalación física, recuperación tras retroceso, imagen y audio aún no están aceptados en la tableta de referencia.

En cada despliegue posterior conserve origen y sumas de la versión, hash del sobre preparado, ID de solicitud, estado nativo antes/después y resultado de aceptación física separado. Una descarga correcta o un comprobante de comando no reemplaza esas observaciones.

</div>
