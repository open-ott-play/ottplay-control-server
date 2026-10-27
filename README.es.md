# OTT-play Control Server

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/15562/badge)](https://www.bestpractices.dev/projects/15562)

[![Fuzzing: RC only](https://img.shields.io/badge/Fuzzing-RC%20only-blue)](docs/fuzzing.md)

[Documentación / Wiki](docs/wiki/Home.es.md)

Un pequeño servidor que puedes alojar tú mismo para entregar órdenes remotas a reproductores OTT-play. Los reproductores realizan solicitudes HTTP salientes, por lo que el televisor no necesita exponer un puerto de escucha. Cada reproductor tiene su propia cola y token de acceso; las órdenes se envían mediante un token de administrador independiente.

El [control fiable](docs/reliable-control.md) opcional añade credenciales de integración con permisos limitados, resultados de órdenes persistentes con almacenamiento acotado, aplicación reanudable de configuraciones Local/Home con verificación exacta de ajustes y transferencia explícita entre reproducción en directo y de archivo. Estas funciones requieren revisiones del controlador y de la CLI que incluyan esta guía. La verificación exacta y la transferencia también requieren un reproductor actualizado. La revisión de instalación antigua fijada más abajo no incluye estas funciones. El [diagrama de arquitectura](docs/assets/reliable-control.svg) muestra dónde permanecen los ajustes, las credenciales y el estado de las operaciones.

El servidor funciona como un único ejecutable Go portátil, con una biblioteca DNS de versión fija para el descubrimiento opcional de servicios. Los paquetes cubren Linux amd64, arm64 y ARMv7; macOS Intel y Apple Silicon; y Windows amd64 y arm64. Las imágenes de contenedor admiten Linux amd64 y arm64. Kubernetes y k3s utilizan el chart Helm incluido.

## Iniciar un servidor

Descarga el archivo correspondiente desde [Releases](https://github.com/open-ott-play/ottplay-control-server/releases), compruébalo con el manifiesto de la versión y extráelo. En Windows, utiliza `ottplay-control-server.exe` en las órdenes siguientes.

```sh
./ottplay-control-server version
./ottplay-control-server init --config config.json --device-id living-room
./ottplay-control-server validate --config config.json
./ottplay-control-server serve --config config.json
```

`init` crea credenciales aleatorias y diferentes en un archivo nuevo que, en Unix, solo permite acceso al propietario. Rechaza sobrescribir un archivo o enlace simbólico existente y nunca imprime las credenciales. Mantén el archivo privado; en Windows, limita su ACL a la cuenta que ejecuta el servidor. `config.example.json` documenta los campos y contiene intencionadamente tokens de ejemplo no válidos.

La dirección predeterminada es `0.0.0.0:8081` (todas las interfaces IPv4, incluida la LAN). Conecta los televisores usando la dirección LAN y el puerto del servidor. Las configuraciones existentes mantienen su valor explícito de `listen`; cámbialo a `0.0.0.0:8081` o utiliza `--listen 0.0.0.0:8081` para exponer una instalación antes limitada a loopback. Añade el **origen exacto de la página** del reproductor a `allowed_origins`, por ejemplo `http://192.168.1.20:8443`. Deben coincidir el esquema, el nombre de host y el puerto; no añadas una ruta. Una lista de orígenes vacía solo permite clientes que no sean navegadores. Los cambios de configuración se aplican al reiniciar. Las credenciales del dispositivo y del administrador siguen siendo obligatorias.

HTTP se admite para navegadores antiguos de televisores en una LAN de confianza. Utiliza HTTPS cuando la conexión cruce una red no fiable: `serve --config config.json --tls-cert server.crt --tls-key server.key`, o termina TLS en un proxy inverso. Las páginas del reproductor servidas por HTTPS no pueden conectarse desde el navegador a un servidor HTTP sin cifrar. Las aplicaciones nativas también mantienen la política de transporte de su sistema operativo; este ajuste no la relaja.

HTTPS directo valida la cadena de certificados suministrada antes de abrir el puerto de escucha: las claves RSA deben tener al menos 2048 bits y las ECDSA, 224 bits; también se admite Ed25519. Sustituye los certificados con claves demasiado pequeñas antes de actualizar. Consulta la [política de certificados TLS y su alcance](docs/security-design.md#direct-https-certificate-policy).

## Conectar el reproductor

Utiliza una versión del reproductor que incluya la conexión al servidor de órdenes. Abre **Ajustes → Control remoto** e introduce:

1. **Dirección del servidor:** su IP o nombre de host, opcionalmente con puerto, o una dirección completa `http://` / `https://`. Un host sin esquema utiliza HTTP y el puerto 8081 de forma predeterminada.
2. **Código de acceso:** el `token` de la entrada de ese reproductor en `devices`, nunca `admin_token`.
3. **Conectar:** activa el sondeo y comprueba el estado que aparece en el mismo diálogo.

El reproductor guarda esta conexión solo en la instalación actual y la excluye de las copias de ajustes y las transferencias en la nube. No necesita el servidor HTTP local independiente. Los navegadores web requieren un origen CORS permitido; las páginas de TV empaquetadas con `Origin: null` pueden utilizar el ajuste explícito `allow_null_origin` para las rutas designadas de dispositivo y arranque inicial. Esto no habilita las rutas de administrador ni del operador de diagnóstico.

## Mando desde el terminal

La [guía ilustrada de Android OTA](docs/android-ota.md) distingue las actualizaciones habituales de Capacitor por Wi-Fi (`ott DEVICE update --variant api22|full`, la última beta por defecto; `--version` selecciona una versión) del agente independiente con acceso root. Explica las actualizaciones firmadas del agente y de APK/auxiliares, la recuperación y la verificación del resultado instalado.

La [guía de Android ADB](docs/android-adb.md) cubre el acceso USB mediante una estación de trabajo o Synology, el control de pantalla y teclado, las capturas, los registros, la instalación de APK y el reinicio normal, incluidas las limitaciones de tabletas antiguas.

El [descubrimiento y emparejamiento DNS-SD](docs/discovery.md) opcional permite que los reproductores encuentren el controlador a través de su dominio de red y reciban un token individual después de `ott approve NAME CODE`. DNS contiene únicamente metadatos del servicio. Las conexiones manuales existentes siguen funcionando sin activar el descubrimiento.

Utiliza la CLI de Python para alias de reproductores, volumen en directo, búsqueda de canales, EPG actual, selección de proveedor y configuración de proveedores compatibles: [guía de la CLI](docs/cli.md). Tanto el servidor como el reproductor deben incluir la extensión de solicitud/respuesta.

La CLI de Python se instala desde una copia del código fuente, por separado del archivo del servidor nativo. Los [pasos de instalación y configuración](docs/cli.md#installation-and-connection) abarcan macOS/Linux y Windows, la configuración de Python/PATH y los módulos necesarios. Fijan un commit revisado del desarrollo 0.1.1 con workbench y verificación sin conexión; la CLI estable v0.1.0 y los archivos del servidor nativo no incluyen esas órdenes. [Actualiza o revierte la CLI](docs/cli.md#update-select-or-roll-back-the-cli) sin sustituir una copia de trabajo, un lanzador o una configuración privada existentes. Los lanzadores locales pueden utilizar el [resolutor de listas de reproducción](docs/cli.md#local-playlist-resolver) de solo lectura para buscar canales, programas actuales y archivos reproducibles sin controlador. Una vez instalado y configurado:

```sh
ott --help                 # all CLI command families
ott devices                # controller registrations and last contact
ott a1                     # this player's status and available controls
ott a1 prev                # previous channel in the active category (previous also works)
ott a1 next                # next channel, wrapping at the end of the category
ott a1 channels "РЕН"       # list channel-name matches without playing
ott a1 "РЕН ТВ HD"          # search and play a matching channel
ott a1 volume 35            # vol/v are equivalent command aliases
ott a1 aspect               # current Fit/Fill mode and saved-setting readback
ott a1 aspect fill          # fill the screen proportionally, cropping the edges
ott a1 aspect "Fit to screen" # full mode name; preserve the whole picture
ott a1 restart             # reload the player
ott a1 restart stream      # restart only the current stream
ott a1 standby             # stop playback and enter player standby; stay connected
ott a1 go                  # leave standby and start configured playback (start/wake also work)
ott a1 screenshot          # save a PNG; browser builds need a selected capture source
ott a1 shot -o screen.png  # short alias; never overwrites an existing file
ott a1 test run media-progress --report ./a1-progress
ott report verify ./a1-progress --json  # re-evaluate saved evidence offline
```

`a1` es un alias de ejemplo de un reproductor registrado, no un nombre de host. Consulta [registro y emparejamiento](docs/cli.md#register-and-connect-a-player), [todas las órdenes con ejemplos](docs/cli.md#commands), [desconexión y revocación de acceso](docs/cli.md#disconnect-deregister-or-revoke-a-player) y [solución de problemas](docs/cli.md#troubleshooting).

Para un reproductor siempre encendido, utiliza [standby y go](docs/cli.md#stop-playback-while-keeping-the-player-online) para detener la reproducción y reanudarla después sin salir de la aplicación. `stop` sigue siendo un alias de `standby`; `start` y `wake` siguen siendo alias de `go`. El control remoto debe mantenerse conectado, por lo que la espera no garantiza que el dispositivo deje de generar tráfico de red. `ott a1` muestra las operaciones anunciadas por el reproductor. La aceptación de una orden no confirma que el apagado o el inicio de la reproducción hayan terminado.

Las [órdenes de relación de aspecto](docs/cli.md#aspect-ratio-fit-and-fill) requieren una CLI y un controlador actualizados y un reproductor que anuncie la capacidad `aspect`. El cambio se aplica después del acuse de recibo; vuelve a leer `aspect` para confirmar el modo y si se guardó. Fill recorta proporcionalmente en lugar de estirar la imagen.

Las [capturas de pantalla](docs/cli.md#remote-screenshots) remotas requieren una CLI y un controlador actualizados y un reproductor con un adaptador de captura compatible. Activar la conexión de Control remoto del reproductor autoriza capturas y diagnósticos para ese controlador; las capturas nativas no necesitan un permiso separado. Los navegadores siguen requiriendo su selector local de fuente. Las imágenes pueden incluir ajustes, pantallas de PIN y credenciales visibles, así que conéctate solo a controladores de confianza. `ott a1` informa de la disponibilidad y de la selección de fuente necesaria. Las capturas son independientes de la recogida de telemetría estructurada.

Para mezclar todos los vídeos de VPortal que coincidan con un título y reproducirlos en bucle:

```sh
ott l vpr "wedding"
```

`vpr` mezcla una vez la selección completa y después repite esa cola. Ejecútalo otra vez para una nueva mezcla. `vp` reproduce las coincidencias en el orden del catálogo; `vp --list` las enumera sin cambiar la reproducción. Consulta las [órdenes de VPortal](docs/cli.md#commands).

Para una secuencia explícita de Plex usando la configuración Plex guardada del reproductor:

```sh
ott l plex preview 78777 78776 78775  # check availability without starting playback
ott l plex play 78777 78776 78775     # starts the first item from zero, in this order
ott l plex queue                     # current item and preparation/playback state
ott l plex next                      # advance once; no wrap at the last item
ott l plex stop                      # stop and clear this queue
ott l plex libraries                 # list libraries (requires plex_library support)
ott l plex shuffle "Tom and Jerry"   # shuffle one complete library, up to 500 items
ott l plex library --preview id:16    # read-only check of an explicit library
```

La CLI espera hasta 45 segundos de forma predeterminada. Escribir `ott -t 45 l plex shuffle "Tom and Jerry"` hace explícito ese mismo tiempo de espera de respuesta; no limita cuánto duran los vídeos. Recopilar bibliotecas Plex grandes puede tardar más que un plazo breve de 10–15 segundos. Pon `-t SECONDS` (o `--timeout SECONDS`) antes del alias del reproductor. Consulta los [tiempos de espera de bibliotecas Plex](docs/cli.md#plex-libraries-by-name) para ejemplos de previsualización y qué hacer si una solicitud de reproducción agota el plazo.

Las colas Plex requieren una CLI, un controlador y un reproductor con `plex_queue`. `plex play --shuffle ID...` mezcla una vez los ID indicados. Las colas se detienen al llegar al último elemento salvo que una política de quiosco existente las repita. Mientras haya una cola, las órdenes normales `next` y `prev` actúan sobre ella; `plex stop` la vacía. Una previsualización nunca cambia de proveedor ni sustituye la reproducción actual. Consulta las [órdenes de cola Plex](docs/cli.md#ordered-plex-queues) para límites y disponibilidad. Buscar bibliotecas requiere además un reproductor y un controlador actualizados que anuncien `plex_library`; usa la conexión Plex guardada del reproductor y rechaza selecciones ambiguas o incompletas. Consulta [bibliotecas Plex por nombre](docs/cli.md#plex-libraries-by-name).

### Guardar perfiles de vídeos locales

Con un reproductor y un controlador actuales, los archivos/carpetas locales utilizan el proveedor `offline`:

```sh
ott d1 provider local
ott d1 profile save 1 "Family movies"
ott d1 profile export 1 family-movies.json
ott d1 profile import 2 family-movies.json
ott d1 profile load 2
```

Conecta primero los archivos/carpetas en el reproductor. Guardar registra su configuración y el vídeo seleccionado sin iniciar la reproducción. La exportación transfiere solo descripciones; los medios y permisos de acceso del sistema/navegador permanecen en el dispositivo. Otra instalación requiere vinculación local, y cargar no implica que la reproducción esté confirmada. Consulta [perfiles de vídeos locales](docs/cli.md#local-videos-profiles) para configuración, capacidades, límites del formato portátil y solución de problemas.

### Guardar el elemento actual entre perfiles

```sh
ott l fav add "Evening cartoons"    # capture this player's current item and profile
ott l fav                           # list saved entries and their stable IDs
ott l fav play "Evening cartoons"  # load that profile, then resolve and play the item
ott l fav rm fav-abc123              # use the actual ID from the list
```

Los favoritos permanecen en el reproductor de destino durante reinicios normales. Seleccionar uno carga su proveedor/perfil guardado y resuelve un enlace nuevo; una fuente cambiada o un elemento ausente causa un error sin sustituirlo por otro canal o película. La CLI, el controlador y el reproductor deben admitir `favorites`; `ott l` muestra las operaciones disponibles. Consulta [favoritos entre perfiles](docs/cli.md#favorites-across-profiles) para alias, límites, semántica de confirmación y solución de problemas.

### Cargar una configuración guardada

Guarda configuraciones con nombre en la sección privada `presets` de `~/.config/ottplay-control/cli.json`. Cada una contiene perfiles M3U numerados (lista y horas de archivo), credenciales Plex y el perfil M3U que se seleccionará al terminar la carga. Consulta el [ejemplo de configuración](docs/cli.md#named-setups).

```sh
ott presets                  # List saved setup names without credentials
ott t1 load local            # Configure a Mac player
ott o1 load local            # Configure a local OTT Server instance
ott l load home              # Configure the living-room TV
ott iphone load home        # Configure the iPhone
ott t1 profile 2             # Select the second saved M3U profile later
ott t1 provider plex         # Open the configured Plex provider later
```

La CLI valida primero toda la configuración y después espera la confirmación del reproductor antes de enviar cada cambio siguiente. Gestiona la carga del proveedor, guarda Plex y las posiciones M3U y selecciona el perfil M3U configurado. Un error detiene la secuencia e informa de los pasos confirmados; una escritura incierta nunca se repite automáticamente. No se necesita un servicio adicional de perfiles ni reiniciar el controlador. Mantén esta configuración privada: contiene credenciales de proveedores.

## Enviar una orden

Este ejemplo lee el token del administrador desde la configuración local privada sin incluirlo en la URL ni en los argumentos de la línea de órdenes:

```python
import json
import urllib.request

with open("config.json", encoding="utf-8") as stream:
    config = json.load(stream)
request = urllib.request.Request(
    "http://127.0.0.1:8081/api/webhook/commands?device_id=living-room",
    data=json.dumps({"command": "set_volume", "volume": 35}).encode(),
    headers={
        "Authorization": "Bearer " + config["admin_token"],
        "Content-Type": "application/json",
    },
)
with urllib.request.urlopen(request, timeout=5) as response:
    print(response.status)
```

Otras órdenes seleccionan un canal por número o nombre, eligen uno aleatorio, cambian de proveedor, muestran un mensaje, modifican una lista M3U si se admite o salen del reproductor. La disponibilidad depende de la plataforma y del proveedor actual. Consulta el [contrato de la API](docs/api.md).

Con un reproductor que admita el proveedor VPortal independiente, `ott a1 provider
vportal` selecciona sus propios 15 perfiles. Utiliza `profile N vportal LINK`, `profile N
name NAME` o un archivo privado `profile-config N FILE` que contenga `name` y `vportal`, y luego selecciona la posición configurada con `profile N`. Los perfiles M3U existentes permanecen separados. `profiles` informa de metadatos sin exponer enlaces de la cuenta ni claves.

Después de iniciar una película o cola de episodios VPortal, `ott a1 kiosk on` bloquea esa selección. El reproductor conserva la cola y su posición durante las recargas y vuelve a resolver las URL de los medios para recuperarse. Utiliza `kiosk off` para elegir otro título o cambiar perfiles y luego `kiosk on` para bloquearlos. El recibo de quiosco muestra el título y el número de episodios; las solicitudes al proveedor y las URL de flujo permanecen en el reproductor.

Utiliza `ott a1 kiosk on --strict` para reproducción infantil: un toque local muestra el pie del vídeo. Los reproductores core actualizados permiten tocar o arrastrar su línea temporal para desplazarse dentro del episodio VPortal/Plex actual; el resto de controles locales permanece bloqueado. Los reproductores antiguos, los quioscos de canales y las envolturas que interceptan los gestos de la línea temporal conservan un pie de solo lectura. Un bloqueo existente mantiene su objetivo. Libéralo desde el controlador con `ott a1 kiosk off`. En sitios web Android, impedir la salida mediante el sistema requiere fijar la aplicación por separado con un PIN; consulta el [modo quiosco](docs/cli.md#kiosk-mode).

## Diagnósticos remotos

Empieza con el [inicio rápido de workbench de solo lectura](docs/workbench.md#quick-start) para `doctor`, `inspect` de interfaz/medios, consulta de recibos de operaciones, paquetes de pruebas, `health`, `media-progress` web/nativo y `report verify` sin conexión. Utiliza la configuración CLI del administrador y los alias existentes. La vía nativa está destinada al agente de mantenimiento Android preparado por separado; los reproductores Tauri usan inspección web. Los informes distinguen el progreso del decodificador de la verificación física de pantalla y sonido. Consulta la [solución de problemas de workbench](docs/workbench.md#troubleshooting) si faltan capacidades, hay vías no disponibles o las pruebas están incompletas.

Los [diagnósticos del protocolo 2](docs/remote-diagnostics.md) opcionales proporcionan sesiones habilitadas explícitamente para cada entorno de ejecución, con credenciales de operador independientes y limitadas, y telemetría estructurada acotada. La conexión habilitada del reproductor aporta autorización local sin otra solicitud de confianza; cada captura sigue teniendo una concesión de sesión limitada. El sondeo de control de diagnóstico es independiente de la cola de órdenes existente.

La [guía de CLI de diagnóstico y MCP](docs/diagnostics-cli.md) cubre instalación, credenciales del operador, conexión, cada orden/herramienta de diagnóstico, ejemplos de captura y reparación, revocación de entornos de ejecución y solución de problemas. `ott diagnostics --help` enumera esta familia separada de órdenes; los alias normales y las credenciales del administrador no conceden acceso al diagnóstico.

## Paquetes de despliegue

- [Tableta Android: quiosco Plex estricto y arranque](docs/android-kiosk-setup.md)
- [Gestión compartida de USB Android y recuperación de arranque](docs/android-usb-manager.md)
- [Docker / Compose, Kubernetes, k3s, systemd, macOS y Windows](docs/deployment.md)
- [Configuración, semántica de entrega y límites](docs/api.md)
- [Proceso de publicación y validación](docs/development.md)

Las colas son limitadas, caducan a los 60 segundos de forma predeterminada y residen en memoria. Reiniciar el servidor descarta las órdenes pendientes. Ejecuta una réplica; las réplicas independientes no comparten colas. Un reproductor confirma un intento de entrega, no la reproducción ni el volumen del hardware. Los reintentos de red se deduplican dentro de una sesión activa del reproductor, no entre reinicios de la aplicación.

Este servidor sigue la API de órdenes del ejemplo opcional `local_proxy.py` de [OTT-play FOSS](https://github.com/open-ott-play/ottplay-foss), con credenciales separadas de administrador/dispositivo y entrega con confirmación. El proxy antiguo sigue siendo un componente independiente. Este proyecto no actúa como proxy de medios ni modifica la red HLS, EPG o de proveedores del reproductor.

## Licencia

MIT. La atribución de las herramientas de publicación incluidas se recoge en `NOTICE` y `LICENSES/`.

## Mantenimiento del proyecto

Consulta los [requisitos de contribución y pruebas](CONTRIBUTING.md), la [política de informes de seguridad](SECURITY.md), el [diseño de seguridad](docs/security-design.md) y las [pruebas OpenSSF y criterios pendientes](docs/openssf-evidence.md).

Las colas Plex aceptan hasta 500 ID en reproductores actuales. Usa `ott f10 plex play --shuffle ID...` para mezclar una vez y luego `ott f10 kiosk on --strict`, con el proveedor Plex seleccionado, para repetir y bloquear la cola. `ott f10 kiosk off` libera el bloqueo. Los reproductores antiguos mantienen el límite que anuncian.

## Fuzzing de versiones candidatas

Las versiones candidatas públicas ejecutan fuzzing nativo acotado sobre los límites de entrada descritos en la [guía de fuzzing](docs/fuzzing.md). Las campañas de mutación no se ejecutan en PR, betas ni compilaciones nocturnas. La guía cubre alcance, presupuestos, pruebas y reproducción de regresiones.

## Comunidad y pruebas de seguridad

Consulta [contribuciones](CONTRIBUTING.md), [gobernanza](GOVERNANCE.md), el [Código de Conducta](CODE_OF_CONDUCT.md), la [hoja de ruta](ROADMAP.md) y las [pruebas OpenSSF](docs/openssf-evidence.md). El índice distingue perfiles de seguridad compatibles, resultados de pruebas reales y requisitos Silver pendientes.
