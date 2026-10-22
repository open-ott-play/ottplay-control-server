# Tableta Android: quiosco estricto de Plex y arranque

<!-- language-navigation:start -->
<p dir="ltr">
<a href="android-kiosk-setup.md" lang="en" dir="ltr">English</a> · <a href="android-kiosk-setup.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="android-kiosk-setup.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="android-kiosk-setup.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="android-kiosk-setup.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="android-kiosk-setup.es.md" lang="es" dir="ltr">Español</a> · <a href="android-kiosk-setup.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="android-kiosk-setup.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="android-kiosk-setup.he.md" lang="he" dir="rtl">עברית</a> · <a href="android-kiosk-setup.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="android-kiosk-setup.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="android-kiosk-setup.it.md" lang="it" dir="ltr">Italiano</a> · <a href="android-kiosk-setup.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="android-kiosk-setup.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="android-kiosk-setup.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="android-kiosk-setup.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="android-kiosk-setup.pt.md" lang="pt" dir="ltr">Português</a> · <a href="android-kiosk-setup.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="android-kiosk-setup.th.md" lang="th" dir="ltr">ไทย</a> · <a href="android-kiosk-setup.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="android-kiosk-setup.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="android-kiosk-setup.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="android-kiosk-setup.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="android-kiosk-setup.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[Guía ADB](android-adb.md) · [OTT Home](../native/android-home/README.md) · [Gestor USB](android-usb-manager.md)

Una tableta dedicada necesita tres ajustes verificados por separado: su cola de contenido, la política de quiosco estricto de OTT y el inicio de Android. Instalar un APK o comprobar apagado/reanudación de pantalla no demuestra el inicio tras reiniciar el sistema.

## Identificar e instalar

1. Registre la tableta física, alias/UUID de OTT, alias del gestor USB, serie, modelo, ID de Android y SDK. Los alias de OTT y USB pueden diferir; no deduzca la identidad del alias ni reutilice el token de otra tableta.
2. Use la variante oficial de APK compatible con el SDK, verifique su SHA-256 publicado y la identidad del paquete/firma, e instale con `install -r`. Conserve los datos. Use el [gestor compartido](android-usb-manager.md) cuando controle USB; no inicie otro servidor ADB ni use comandos `adb` sin dispositivo explícito.
3. Empareje el reproductor con sus propias credenciales del controlador y compruebe `ott TABLET`. Cargue el preajuste nombrado deseado y ejecute `ott TABLET load PRESET verify`. Los preajustes domésticos y el de localhost de un equipo de desarrollo no son intercambiables.

## Verificar Plex desde la tableta

```sh
ott TABLET plex libraries
ott TABLET plex library --preview 'LIBRARY NAME'
```

Compruebe ID, título y número de elementos de la biblioteca. Una solicitud correcta desde el ordenador del administrador no prueba la conectividad de la tableta. Ante fallos, revise en ella dirección guardada, rutas, TLS y credenciales. Prefiera el endpoint HTTPS válido del servidor. Si el firmware antiguo requiere HTTP en la LAN privada, limítelo a esta instalación, documente que tokens y medios viajan sin cifrar y no cambie un preajuste compartido ni desactive globalmente la verificación de certificados. Nunca publique tokens ni URL de medios privados en diagnósticos o documentación.

Cambiar la dirección del servidor Plex borra las raíces de biblioteca seleccionadas. Elija el proveedor **Plex**, abra **Plex settings → Folders**, marque la fila de la biblioteca deseada (no solo **Browse folders**), elija **Ok** y luego **Save and open library**. En el reproductor `1.1.58-beta.19`, las credenciales por sí solas pueden abrir el selector mientras `ready` sigue siendo false; complete la selección antes de usar comandos de cola. No cambie a M3U solo para obtener `ready: true`: una cola Plex puede reproducirse allí, pero `kiosk on --strict` puede guardar una política M3U en espera sin medios vinculados. Eso no bloquea ni restaura la cola Plex.

## Iniciar y bloquear la cola

```sh
ott TABLET kiosk off
ott TABLET plex shuffle 'LIBRARY NAME'
ott TABLET plex status
ott TABLET debug --engine
# Continue only after the intended queue is playing.
ott TABLET kiosk on --strict
ott TABLET plex status
ott TABLET kiosk status
ott TABLET debug --engine
```

Para una cola ordenada use `plex library 'LIBRARY NAME'` en lugar de `plex shuffle`. Shuffle elige un orden una vez; el quiosco lo repite. Confirme los elementos deseados, `enabled: true`, `strict: true` y una política vinculada a la cola Plex correcta. Una política `waiting` sin canal ni medios no es un quiosco aceptado. Observe al menos dos muestras con avance de posición/contador de fotogramas e inspeccione la pantalla. Un acuse o la etiqueta `playing` sin avance no bastan. Pruebe una transición natural al terminar un elemento; si no es práctico comprobar todo el bucle, registre esa limitación explícitamente.

El quiosco estricto controla la interfaz del reproductor. No es inscripción como propietario de dispositivo Android ni demuestra que Home, Recents, notificaciones y Settings estén bloqueados. Mantenga el lanzador del sistema y una vía de mantenimiento supervisada.

## Habilitar el arranque por separado

Compile y verifique [OTT Home 0.2.0](../native/android-home/README.md), conservando su clave de firma para actualizaciones. Instale `play.ott.home` junto a `play.ott.foss`. Abra `play.ott.home/.StartupActivity` y active **Start OTT after reboot**. La opción está desactivada por defecto. No modifica el binario oficial, su firma OTA, perfiles ni asignación del botón Home.

Si el firmware tiene selector Home, también puede elegir **OTT Home → Always**. Si no existe (incluido el firmware Fire antiguo probado), la activación de arranque funciona independientemente. Las instalaciones previamente configuradas como Home predeterminado conservan su comportamiento. Desactive ambos mecanismos para una reversión completa.

`recover_on_boot` del gestor USB compartido es un tercer mecanismo independiente: necesita NAS y cable USB, solo trata un arranque nuevo y no reinicia continuamente aplicaciones fallidas. No sustituye al arranque autónomo. Para probar este último, mantenga desactivada la recuperación para que no oculte un fallo de inicio de la aplicación acompañante. Respete los bloqueos seguros; desbloquee normalmente si se solicita.

## Aceptación y entrega

Guarde las evidencias en privado, sin credenciales en recibos públicos:

- Versiones y hashes de APK, correspondencia de identidades física/OTT/USB y preajuste elegido.
- Biblioteca Plex, cantidad/orden de elementos, política estricta y reproducción inicial con avance.
- ID de arranque antes y después de un reinicio real del sistema; después, `sys.boot_completed=1`.
- Ningún inicio manual del reproductor ni recuperación USB entre reinicio y observación.
- Actividad OTT en primer plano, reconexión al controlador, cola/política estricta restaurada y reproducción avanzando. El resultado `requested` del receptor de arranque solo indica envío.
- Prueba aparte del botón de encendido: pantalla apagada, audio detenido, desbloqueo normal y reanudación.
- Límites precisos: desbloqueo seguro requerido, recuperación asistida por USB, salidas físicas sin probar, fallos de flujo o falta de memoria. No dé esos casos por totalmente aceptados.

Antes de entregar otra tableta, repita la lista en vez de copiar el éxito de la anterior. Las Fire antiguas pueden necesitar `hls.js` para M3U porque el HLS nativo del fabricante puede fallar; eso no demuestra estabilidad de Plex.

## Mantenimiento y reversión

```sh
ott TABLET kiosk off
```

Abra **OTT Startup**, desactive **Start OTT after reboot** y restaure la selección original de Home si se cambió. Si hace falta, desinstale solo `play.ott.home` por el transporte USB verificado; conserve `play.ott.foss` y sus datos. Desactivar el arranque no altera la cola ni desactiva automáticamente el quiosco.
