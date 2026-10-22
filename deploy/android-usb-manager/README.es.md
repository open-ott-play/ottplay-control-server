# Imagen de transporte USB de Android

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

Este directorio contiene las plantillas de despliegue y el [`Dockerfile`](Dockerfile) del contenedor de transporte del gestor compartido. El gestor Python se ejecuta en el host USB. La imagen proporciona ADB, Python y las herramientas del adaptador de mantenimiento atómico; no contiene un segundo registro de dispositivos ni un demonio de recuperación. Consulte la [guía del operador](../../docs/android-usb-manager.md) para el aprovisionamiento, los comandos, la migración, los límites de recuperación y la reversión.

Sin un checkout de Git, descargue `ottplay-control-server-deployment.tar.gz` y `release-manifest.json` de la misma [versión](https://github.com/open-ott-play/ottplay-control-server/releases). Verifique el digest del archivo y extráigalo en un directorio nuevo según [Obtener el paquete de despliegue](../../docs/android-usb-manager.md#get-the-deployment-package). El paquete incluye esta receta y los seis scripts del host/remotos en `tools/`; conserve su estructura. Los paquetes anteriores, incluido `v0.1.5-beta.6`, carecen de esos scripts y requieren el archivo de fuentes correspondiente. No se incluye una imagen de transporte USB ya construida. El artefacto OCI de la versión del servidor tiene otra finalidad.

## Procedencia del código y los artefactos

La base es `python:3.12.14-slim-trixie`, fijada a `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`. La base, la instrucción APT y la instalación de `websocket-client==1.9.0` bloqueada por hash se revisaron en [PR 128, revisión b4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager). Se conserva el texto de las instrucciones para reutilizar una caché de compilación disponible. El paquete WebSocket opcional admite clientes genéricos de mantenimiento existentes; los adaptadores CDP migrados también pueden usar solo la biblioteca estándar de Python.

Esta receta elimina el paquete `pip` instalado después de instalar el wheel fijado, excluye el código de gestión y recuperación de PR 128, inicia ADB en primer plano y desactiva las comprobaciones de salud heredadas. Quitar el instalador en ejecución reduce su superficie de ataque disponible; no demuestra un análisis de vulnerabilidades sin hallazgos.

El artefacto revisado de PR 128 era `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`. Contiene otro punto de entrada y una comprobación de salud por latido incompatible. Es un artefacto de referencia, no una compilación de este Dockerfile. No atribuya a esta receta su ID de imagen, procedencia del código ni resultados del análisis.

Los repositorios APT no están fijados a instantáneas. Una futura reconstrucción limpia puede elegir paquetes del sistema más recientes incluso con el mismo digest de base y hash del wheel de Python. Al validar una compilación, registre la revisión exacta, el constructor/plataforma, el ID de imagen resultante, las versiones de paquetes y el análisis. La configuración de producción debe usar ese ID de imagen inmutable validado o su digest.

## Compilar sin perturbar un host USB ocupado

Prefiera compilar y analizar en un constructor separado. El host de destino solo necesita la imagen validada, no descargas de paquetes ni herramientas de compilación.

Al reutilizar un constructor local preparado, compruebe primero que dispone de la imagen base exacta y que las capas de instalación APT y wheel están en caché. Cargar únicamente la imagen final de PR 128 no demuestra ninguna de las dos condiciones. Desde la raíz del repositorio o del directorio de despliegue extraído, este es un intento deliberadamente sin conexión:

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none` se aplica a los pasos de compilación; no garantiza que el constructor nunca contacte un registro al resolver la base. Confirme la resolución local de la base antes de usar este procedimiento en un host limitado. Si falta una capa APT o wheel, detenga el intento en vez de permitir silenciosamente una reconstrucción por red en el host USB.

BuildKit incluye el modo de red del paso en la clave de caché. Por tanto, una caché creada con el modo de red predeterminado puede no acertar en el intento sin conexión anterior. Reutilícela solo tras comprobar su base exacta y la cadena de instrucciones; deténgase si algún paso de instalación se ejecuta en vez de indicar `CACHED`. La eliminación final de `pip` usa explícitamente `RUN --network=none`; no requiere red aunque los pasos anteriores en caché mantengan su modo original.

Antes de cambiar la configuración de producción, compruebe las versiones de Python y ADB, analice la imagen resultante exacta, verifique la transferencia de archivos binarios y la preparación del APK y realice las pruebas de aceptación de identidad del dispositivo, autorización de claves, suspensión y arranque de la guía. Es necesario probar ADB 34 con un Android antiguo; el número de versión no demuestra compatibilidad con KitKat o Fire. Conserve la imagen actual y la copia privada del despliegue hasta que ya no se necesite la reversión.

## Presupuesto de ejecución

El gestor del host, no este Dockerfile, aplica 256 MiB de memoria, 256 MiB de memoria más swap y CPU shares 128, sin cuota estricta de CPU o PID. Impone `--no-healthcheck`, `--network none`, un sistema raíz de solo lectura, capacidades eliminadas y asignaciones exactas de nodos USB. `/tmp` es un tmpfs limitado que consume el mismo presupuesto de memoria. El adaptador de preparación escribe bloques de 1 MiB y limita cada carga a 128 MiB; las operaciones comparten el bloqueo del gestor.

Docker documenta la diferencia entre los [controles de memoria, swap y CPU shares](https://docs.docker.com/engine/containers/resource_constraints/) y la [opción `--no-healthcheck`](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck).
