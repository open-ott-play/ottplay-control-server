# Android USB 전송 이미지

<!-- language-navigation:start -->
<p dir="ltr">
<a href="README.md" lang="en" dir="ltr">English</a> · <a href="README.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="README.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="README.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="README.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="README.es.md" lang="es" dir="ltr">Español</a> · <a href="README.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="README.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="README.he.md" lang="he" dir="rtl">עברית</a> · <a href="README.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="README.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="README.it.md" lang="it" dir="ltr">Italiano</a> · <a href="README.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="README.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="README.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="README.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="README.pt.md" lang="pt" dir="ltr">Português</a> · <a href="README.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="README.th.md" lang="th" dir="ltr">ไทย</a> · <a href="README.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="README.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="README.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="README.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="README.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

이 디렉터리에는 공유 관리자의 전송 컨테이너용 배포 템플릿과 [`Dockerfile`](Dockerfile)이 있습니다. Python 관리자는 USB 호스트에서 실행됩니다. 이미지는 ADB, Python과 원자적 유지보수 어댑터의 도구를 제공하며, 두 번째 장치 레지스트리나 복구 데몬을 포함하지 않습니다. 준비, 명령, 마이그레이션, 복구 제한과 롤백은 [운영자 가이드](../../docs/android-usb-manager.md)를 참조하세요.

Git 체크아웃이 없다면 같은 [릴리스](https://github.com/open-ott-play/ottplay-control-server/releases)에서 `ottplay-control-server-deployment.tar.gz`와 `release-manifest.json`을 받으세요. 아카이브 다이제스트를 검증하고 [배포 패키지 받기](../../docs/android-usb-manager.md#get-the-deployment-package)에 따라 새 디렉터리에 푸세요. 패키지는 이 레시피와 `tools/`의 호스트/원격 스크립트 여섯 개를 포함합니다. 디렉터리 구조를 유지하세요. `v0.1.5-beta.6`을 비롯한 이전 패키지에는 해당 스크립트가 없어 일치하는 소스 아카이브가 필요합니다. 빌드된 USB 전송 이미지는 포함되지 않습니다. 컨트롤 서버의 OCI 릴리스 자산은 용도가 다릅니다.

## 소스와 산출물의 출처

기반은 `python:3.12.14-slim-trixie`이며 `sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b`로 고정됩니다. 기반 이미지, APT 명령과 해시로 고정된 `websocket-client==1.9.0` 설치는 [PR 128의 리비전 b4181bf](https://github.com/open-ott-play/ottplay-control-server/tree/b4181bff35faff9a27c9bc1e86b0004c43ad1efc/deploy/android-manager)에서 검토되었습니다. 사용 가능한 빌드 캐시를 재사용하도록 명령 텍스트를 유지합니다. 선택적인 WebSocket 패키지는 기존 범용 유지보수 클라이언트를 지원합니다. 마이그레이션된 CDP 어댑터는 Python 표준 라이브러리만 사용할 수도 있습니다.

이 레시피는 고정된 wheel을 설치한 뒤 설치된 `pip` 패키지를 제거하고, PR 128의 관리자 및 복구 코드를 제외하며, ADB를 포그라운드에서 시작하고, 상속된 상태 검사를 비활성화합니다. 런타임 설치 프로그램 제거는 공격 표면을 줄이지만 취약점 검사에서 문제가 없었다는 증거는 아닙니다.

검토된 PR 128 산출물은 `sha256:f2ed00bac5b137c1a4bd18db8f7675677e7c36d4444618eec8f996e205531a6a`였습니다. 다른 진입점과 호환되지 않는 하트비트 상태 검사를 포함합니다. 이는 참조 산출물이며 이 Dockerfile의 빌드가 아닙니다. 해당 이미지 ID, 소스 출처나 검사 결과를 이 레시피에 귀속시키지 마세요.

APT 저장소는 스냅샷에 고정되지 않았습니다. 따라서 기반 다이제스트와 Python wheel 해시가 같더라도 향후 깨끗한 재빌드는 더 새로운 OS 패키지를 선택할 수 있습니다. 빌드를 승인할 때 정확한 소스 리비전, 빌더/플랫폼, 결과 이미지 ID, 패키지 버전과 검사 결과를 기록하세요. 운영 설정은 승인된 불변 이미지 ID 또는 다이제스트를 사용해야 합니다.

## 사용 중인 USB 호스트를 방해하지 않고 빌드하기

별도 빌더에서 빌드하고 검사하는 것을 권장합니다. 대상 호스트에는 승인된 이미지만 필요하며, 패키지 다운로드나 빌드 도구 모음은 필요 없습니다.

준비된 로컬 빌더를 재사용할 때는 정확한 기반 이미지가 있고 APT 및 wheel 설치 레이어가 캐시되어 있는지 먼저 확인하세요. PR 128의 최종 이미지만 로드해서는 어느 조건도 입증되지 않습니다. 저장소 루트나 압축을 푼 배포 디렉터리에서 의도적으로 오프라인 시도를 하려면 다음을 실행하세요.

```sh
docker image inspect python:3.12.14-slim-trixie@sha256:44ff437bba879d4941b710a369a8f19266aea34b29002807f0c487fabc9eec9b
docker build --pull=false --network=none \
  --tag ott-android-transport:review \
  deploy/android-usb-manager
docker image inspect ott-android-transport:review
```

`--network=none`은 빌드 단계에 적용됩니다. 기반 이미지를 해석할 때 빌더가 레지스트리에 절대 접속하지 않는다는 일반적 보장은 아닙니다. 자원이 제한된 호스트에서 이 절차를 쓰기 전에 기반 이미지가 로컬에서 해결되는지 확인하세요. APT 또는 wheel 레이어가 없으면 USB 호스트에서 조용히 네트워크 재빌드를 허용하지 말고 중단하세요.

BuildKit은 빌드 단계의 네트워크 모드를 캐시 키에 포함합니다. 기본 네트워크 모드로 만든 캐시는 위 오프라인 시도에서 적중하지 않을 수 있습니다. 정확한 기반과 명령 체인을 확인한 뒤에만 재사용하고, 패키지 설치 단계가 `CACHED` 대신 실행되면 멈추세요. 최종 `pip` 제거는 명시적으로 `RUN --network=none`을 사용하므로 앞선 캐시 단계가 원래 모드를 사용해도 네트워크가 필요 없습니다.

운영 설정을 바꾸기 전에 Python과 ADB 버전을 확인하고, 정확한 결과 이미지를 검사하고, 바이너리 파일 전송과 APK 스테이징을 검증한 뒤 가이드의 장치 신원, 키 승인, 절전과 부팅 인수 검사를 수행하세요. ADB 34와 구형 Android 장치의 통신은 테스트해야 합니다. 버전 번호만으로 KitKat 또는 Fire 호환성이 입증되지 않습니다. 롤백이 필요 없어질 때까지 현재 이미지와 비공개 배포 백업을 보관하세요.

## 런타임 자원 한도

이 Dockerfile이 아니라 호스트 관리자가 메모리 256 MiB, 메모리와 스왑 합계 256 MiB, CPU shares 128을 적용하며 CPU나 PID의 하드 쿼터는 없습니다. `--no-healthcheck`, `--network none`, 읽기 전용 루트 파일 시스템, capability 제거와 정확한 USB 노드 매핑을 강제합니다. `/tmp`는 같은 메모리 예산을 소비하는 제한된 tmpfs입니다. 스테이징 어댑터는 1 MiB 단위로 쓰고 단일 페이로드를 128 MiB로 제한합니다. 작업은 관리자 잠금을 공유합니다.

Docker는 [메모리, 스왑 및 CPU shares 제어](https://docs.docker.com/engine/containers/resource_constraints/)와 [`--no-healthcheck` 옵션](https://docs.docker.com/reference/cli/docker/container/run/#no-healthcheck)의 차이를 문서화합니다.
