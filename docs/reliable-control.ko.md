<div lang="ko" dir="ltr">

# 신뢰할 수 있는 제어와 재생 넘겨주기

<!-- language-navigation:start -->
<p dir="ltr">
<a href="reliable-control.md" lang="en" dir="ltr">English</a> · <a href="reliable-control.ar.md" lang="ar" dir="rtl">العربية</a> · <a href="reliable-control.bn.md" lang="bn" dir="ltr">বাংলা</a> · <a href="reliable-control.cs.md" lang="cs" dir="ltr">Čeština</a> · <a href="reliable-control.de.md" lang="de" dir="ltr">Deutsch</a> · <a href="reliable-control.es.md" lang="es" dir="ltr">Español</a> · <a href="reliable-control.fa.md" lang="fa" dir="rtl">فارسی</a> · <a href="reliable-control.fr.md" lang="fr" dir="ltr">Français</a><br>
<a href="reliable-control.he.md" lang="he" dir="rtl">עברית</a> · <a href="reliable-control.hi.md" lang="hi" dir="ltr">हिन्दी</a> · <a href="reliable-control.id.md" lang="id" dir="ltr">Bahasa Indonesia</a> · <a href="reliable-control.it.md" lang="it" dir="ltr">Italiano</a> · <a href="reliable-control.ja.md" lang="ja" dir="ltr">日本語</a> · <a href="reliable-control.ko.md" lang="ko" dir="ltr">한국어</a> · <a href="reliable-control.nl.md" lang="nl" dir="ltr">Nederlands</a> · <a href="reliable-control.pl.md" lang="pl" dir="ltr">Polski</a><br>
<a href="reliable-control.pt.md" lang="pt" dir="ltr">Português</a> · <a href="reliable-control.ru.md" lang="ru" dir="ltr">Русский</a> · <a href="reliable-control.th.md" lang="th" dir="ltr">ไทย</a> · <a href="reliable-control.tr.md" lang="tr" dir="ltr">Türkçe</a> · <a href="reliable-control.uk.md" lang="uk" dir="ltr">Українська</a> · <a href="reliable-control.vi.md" lang="vi" dir="ltr">Tiếng Việt</a> · <a href="reliable-control.zh-Hans.md" lang="zh-Hans" dir="ltr">简体中文</a> · <a href="reliable-control.zh-Hant.md" lang="zh-Hant" dir="ltr">繁體中文</a>
</p>
<!-- language-navigation:end -->

[문서 색인](wiki/Home.md)

이 기능에는 이 안내서를 포함한 리비전의 컨트롤러와 CLI가 필요합니다. 정확한 설정 비교와 넘겨주기에는 업데이트된 플레이어도 필요합니다. 이전 릴리스 압축 파일과 설치 안내서의 과거 CLI 리비전에 이 기능이 자동으로 추가되지는 않습니다. `preset_session.py`를 포함한 **모든 `cli/*.py` 파일을 함께** 설치하고 비공개 설정은 보존하세요.

![제어, 체크포인트, 넘겨주기 흐름](assets/reliable-control.svg)

## 제한된 영구 결과 저장 활성화

비공개 서버 설정에 이 선택적 블록을 추가하고 검증한 뒤 서버를 재시작하세요. 서비스 계정이 쓸 수 있는 로컬 영구 파일 시스템의 절대 경로를 선택하세요. Unix에서는 디렉터리가 비공개(0700)여야 하며 기존 저널과 잠금 파일은 일반 비공개 파일(0600)이어야 합니다. Windows에서는 디렉터리 ACL을 서비스 계정으로 제한하세요. 저널 하나는 프로세스 하나가 소유합니다.

```json
"journal": {
  "path": "/var/lib/ottplay-control/private/operations.json",
  "max_records": 1024,
  "retention_seconds": 86400
}
```

서버는 없는 디렉터리를 0700 모드로 만듭니다. 제한은 레코드 1–4096개, 60–86400초, 디스크 4 MiB입니다. 보존 기간은 `command_ttl_seconds`를 포함해야 합니다. 저널이 가득 차면 유효한 레코드를 제거하는 대신 새 키 포함 작업을 거부합니다. 손상된 파일, 안전하지 않은 Unix 디렉터리, 다른 기록 프로세스 또는 영구 쓰기 실패가 있으면 안전하게 거부합니다. 기본값은 기존 메모리 내 명령 경로입니다.

동봉된 systemd 유닛은 `/var/lib/ottplay-control`을 0700 모드로 만듭니다. Compose에서는 UID 65532 소유의 호스트 디렉터리를 준비하고 `CONTROL_SERVER_STATE`를 절대 경로로 설정한 뒤 `deploy/compose.yml`과 `deploy/compose.journal.yml`을 함께 사용하세요. 후자는 쓰기 가능한 상태 마운트만 추가하며 루트 파일 시스템은 읽기 전용으로 유지됩니다. Helm에서는 영구 볼륨 클레임을 별도로 준비하고 `journalExistingClaim=CLAIM_NAME`을 설정하세요. 클레임은 `/var/lib/ottplay-control`에 마운트되며 위의 비공개 하위 디렉터리를 사용합니다. 차트는 복제본 하나, `Recreate`, `fsGroupChangePolicy: OnRootMismatch`를 유지하여 다시 마운트할 때 이미 만든 비공개 디렉터리 권한을 재귀적으로 넓히지 않습니다. 선택한 스토리지 드라이버가 이 소유권 의미를 지원하는지 확인하세요. 차트는 클레임을 만들거나 삭제하지 않으며 마운트를 켠다고 저널이 켜지지는 않습니다. 여전히 비공개 서버 설정이 제어합니다.

`POST /api/requests`에서는 `action`, `params` 옆에 `idempotency_key`를 추가하세요. 레거시 명령 POST는 대신 `Idempotency-Key`를 받습니다. 키에는 ASCII 문자, 숫자, 밑줄 또는 하이픈 16–80개가 들어가며 의도마다 새로운 임의 키를 생성해야 합니다. 같은 소유자/키에 장치, 작업, 정규화된 JSON 페이로드도 같으면 다시 큐에 넣지 않고 원래 ID를 반환합니다. 페이로드를 변경하면 409를 반환합니다. 키 범위는 장치가 아니라 운영자 ID(또는 관리자)입니다. 보존 기간 이후에는 키를 새 의도로 받을 수 있으므로 클라이언트는 해당 기간 밖에서 이전 의도를 재시도하면 안 됩니다.

`GET /api/operations?device_id=DEVICE`는 저널 활성화 여부, 제한, 명령 TTL을 보고합니다. 작업을 읽으려면 `&key=KEY`를 추가하세요.

- `pending`: 수락됨. 보존된 응답은 아직 없습니다.
- `responded`: 검증된 플레이어 응답이 도착했으며 `result_status`는 `ok`, `rejected`, `unsupported` 중 하나입니다. 원래 결과 본문은 정상적으로 만료됩니다.
- `acknowledged`: 레거시 소비자가 명령을 확인했습니다. 이는 눈에 보이는 재생이나 설정 변경 저장을 증명하지 않습니다.
- `unknown`: 기한이 지났거나 응답 보존 전에 서버가 재시작되었습니다. 서버는 재시작 후 이 명령을 **절대 재실행하지 않습니다**.

키를 포함한 POST를 반복하면 `status: retained`, 원래 ID와 상태를 반환합니다. 일반적인 새 POST는 `status: queued`를 유지합니다. 저널 레코드에는 소유자, 장치, 작업, 임의 ID, 페이로드 다이제스트, 상태와 기한만 있습니다. 명령 페이로드, 공급자 URL, 토큰, 스크린샷, 응답 본문은 없습니다. 대기 명령은 영구 저장하지 않습니다. 이는 제한된 중복 제거와 결과 추적이며, 정확히 한 번 실행, 영구 작업 큐 또는 다중 서버 고가용성이 아닙니다.

## Local/Home 계획, 적용, 재개, 검증

기존 `ott PLAYER load PRESET` 동작은 유지됩니다. 새 작업 흐름은 다음과 같습니다.

```sh
ott tv load Local plan
ott tv load Local apply
ott tv load Local resume
ott tv load Local verify
```

`plan`은 오프라인으로 로컬 프리셋을 검증하고 설정 슬롯과 원하는 활성 프로필을 내용 출력 없이 나열합니다. 플레이어는 검사하지 않습니다. `verify`는 정확한 읽기 전용 비교를 수행하고 선택한 M3U 프로필을 확인합니다. 차이가 있거나 비교가 지원되지 않으면 0이 아닌 코드를 반환합니다.

`apply`에는 서버 저널이 필요합니다. 비공개 체크포인트를 만들고 첫 변경 전에 구성된 모든 공급자를 비교합니다. 저장할 때마다 `settings_compare`로 저장된 바이트와 예상 값을 비교하고 일치하면 쓰기를 생략합니다. 최종 검증에는 Plex, 모든 M3U 슬롯, 선택적 Stalker 슬롯과 선택한 M3U 프로필이 포함됩니다. 비교 결과는 불리언이며 저장된 비밀 정보는 반환하지 않습니다.

기본 체크포인트는 `~/.config/ottplay-control/checkpoints/` 아래에 있습니다. 모드 뒤에 `--checkpoint /absolute/private/path/session.json`을 넣어 다른 파일을 선택하세요. 체크포인트에는 장치, 프리셋 이름, 서버/프리셋 리비전의 해시, 완료한 단계 이름, 대기 중인 임의 작업 키 하나와 기한이 들어갑니다. 설정 값은 없습니다. 파일은 fsync 후 원자적으로 교체하고 동시 CLI 기록을 막도록 잠급니다. 해당 로컬 프리셋을 계속 사용할 수 있게 유지하세요. Unix에서 디렉터리는 비공개(0700), 일반 파일은 0600이어야 합니다. Windows에서는 상속된 디렉터리 ACL을 CLI 실행 사용자로 제한하세요. Unix 모드 비트는 Windows 접근 권한을 강제하지 않습니다.

`resume`은 같은 서버, 장치와 프리셋 리비전을 검증한 후 대기 결과를 읽습니다. 보존된 응답이 있으면 즉시 재확인할 수 있습니다. 불확실한 결과는 이전 명령 기한, 요청 시간 제한과 작은 여유 시간이 모두 지날 때까지 변경 전송을 중단합니다. 그 이후에 `resume`을 다시 실행하세요. 완료한 단계도 저장된 설정을 다시 확인하여 사용자 변경을 이전 쓰기 성공으로 오인하지 않습니다. 상대 명령은 무조건 재실행하지 않습니다. 절대적인 공급자/프로필 선택은 보호 대기 후 반복할 수 있습니다.

새 `apply`는 기존 체크포인트를 절대 덮어쓰지 않습니다. `resume`으로 재사용하거나 바뀐 프리셋에는 새 파일을 선택하세요. 장치마다 체크포인트가 별도입니다. 모든 플레이어에 걸친 트랜잭션이나 롤백은 없으므로 나중 단계가 실패해도 이전의 성공한 쓰기는 유지됩니다. `verified`는 비교 시점에 플레이어에 저장된 값을 확인할 뿐, 네트워크 로딩 성공이나 실제 영상/음성 출력을 확인하지 않습니다.

## 통합에 필요한 권한만 부여하기

HA, RF 브리지 또는 에이전트에는 `admin_token`을 배포하는 대신 [일반 명령 운영자](security-design.md#scoped-command-operators)를 사용하세요. 정확한 장치 ID와 `input.channel_up`, `input.channel_down` 같은 작업을 허용하세요. 이 프리셋 작업 흐름의 설정 작업에는 `settings_compare`, `provider`, `profiles`, `status`, `profile`, `profile_settings`, `provider_settings` 권한이 명시적으로 필요합니다. 넘겨주기에는 출발지의 `playback_handoff_export`와 대상의 `playback_handoff_import`가 필요합니다. 운영자 항목은 나열된 장치와 작업을 결합하므로 같은 작업을 모든 장치에 허용하면 안 될 때는 별도 운영자 신원을 사용하세요. 토큰과 진단 동의는 별도 권한으로 유지됩니다.

운영자는 플레이어 큐를 폴링하거나 플레이어 응답을 제출하거나 모든 장치를 나열하거나 관리자 디버그 출력 또는 다른 운영자의 결과를 읽을 수 없습니다. 작업 범위를 좁히면 보존된 해당 작업 읽기도 막힙니다. 운영자를 제거하고 재시작하면 자격 증명이 취소됩니다. 이전 저널 레코드가 남아 있는 동안 그 ID를 다른 주체에게 재사용하지 마세요.

## 다른 플레이어에서 계속하기

```sh
ott handoff living-room bedroom
```

출발지는 버전 1 실시간/보관 참조를 내보냅니다. 공급자, 불투명한 소스 신원, 불투명한 항목 지문 `channel-v1:<8 hex>.<8 hex>` 및 보관 콘텐츠라면 현재 프로그램 시간이 포함됩니다. 원시 공급자 ID와 스트림 URL은 로컬에 남습니다. 대상에는 **같은 소스/계정이 구성**되어야 하며 자기 카탈로그에서 유일하게 일치하는 항목을 찾고 자체 로컬 자격 증명으로 새 URL을 받아야 합니다. 컨트롤러는 재생 가능한 URL을 복사하거나 대상 자격 증명을 바꾸지 않습니다. 출발지는 계속 재생합니다. CLI 성공은 대상이 요청을 전송했다는 뜻이며 물리 화면이나 스피커를 검증했다는 뜻은 아닙니다.

버전 1은 플레이어가 필요한 신원을 제공할 수 있는 `m3u`, `xtream`, `stalker` 실시간/보관 카탈로그를 지원합니다. 알 수 없거나 모호한 항목, 소스 불일치, 로컬 정책 거부는 이름으로 대체 검색하지 않고 실패합니다. VOD/시리즈, 다른 계정 간 매핑, 출발지 일시 정지와 지속적인 기록/즐겨찾기 동기화는 첫 계약에 포함되지 않습니다.

## 검증 및 복구 연습

`go test -race ./...`, `go vet ./...`, `python3 -m unittest discover -s tests`를 실행하세요. 테스트는 성공한 쓰기 후 응답 유실, 재개 전 정확한 비교, 설정 드리프트, 알 수 없는 작업 기한, 기록 프로세스 둘, 손상된 저널, 운영자 간 읽기와 잘못된 넘겨주기 페이로드를 다룹니다. 스테이징 플레이어에서는 설정 저장 후 CLI를 중단하고 컨트롤러를 재시작한 뒤 재개하고 검증하세요. 일치하는 소스와 일치하지 않는 소스 간 넘겨주기도 시험하세요. 영상/음성은 별도로 확인하세요.

현재 FOSS 작업 사본과 개발 의존성이 설치된 상태에서 저장소 간 로컬 검증을 하려면 다음을 실행하세요.

```sh
node scripts/smoke_foss_roadmap.cjs /path/to/ottplay-foss
```

스모크 테스트는 이 Go 서버를 빌드하고 전체 FOSS TypeScript 프로젝트를 컴파일하며 실제 전송 계층, 명령 처리기, 소스 신원과 제공되는 재생목록 코덱을 Python CLI와 함께 실행합니다. 지원되는 세 공급자의 정확한 설정, 불일치, 다른 로컬 행 ID를 사용한 실시간 넘겨주기, 보관 탐색 오프셋, URL 확인 실패와 엄격한 전송 형식 검증을 확인합니다. 스토리지, 공급자 정책과 재생 호스트 인터페이스만 테스트 대역이며 브라우저, 실제 미디어 출력이나 실제 계정 접근은 검증하지 않습니다. 루프백에 바인딩하고 비공개 임시 파일을 쓰며 스트림 URL에 접속하지 않고 종료 시 파일과 서버를 정리합니다.

</div>
