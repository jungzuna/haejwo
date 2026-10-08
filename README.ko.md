<p align="center">
  <img src="assets/haejwo.png" width="520" alt="해줘 — 드러누운 비싼 모델과 일하는 워커 티어">
</p>

<h1 align="center">해줘</h1>

<p align="center"><strong>haejwo — "just handle it."</strong><br><em>당신은 말만 하세요. 나머지는 모델들이 알아서 합니다.</em></p>

<p align="center"><sub><a href="README.md">English</a></sub></p>

haejwo는 [Claude Code](https://claude.com/claude-code)와 [Codex](https://github.com/openai/codex)용 훅·규칙 플러그인입니다. 세션 시작 때 규칙을 주입하고, 메인 에이전트의 코드 편집이 턴당 예산을 넘으면 거부해 구현을 워커로 넘기며, 켜 두면 계획을 상대 회사 모델이 리뷰합니다. 세션 모델을 판단에만 쓰고 싶은 두 하네스 사용자를 위한 것입니다.

아무리 대충 적어도(그게 "해줘") 호스트가 계획·위임·검증합니다. 깔면 바로 켜지고 워크플로 명령어는 없습니다. 설정은 선택이며 하기 전까지 안내가 반복됩니다.

## 설치

`python3` 필요(CI는 3.10). 리뷰 러너는 Bash와 git도 씁니다.

**Claude Code:**
```
/plugin marketplace add jungzuna/haejwo
/plugin install haejwo@haejwo
/reload-plugins   # 열린 세션이 있을 때만 (규칙은 재시작 필요)
/haejwo:setup     # 선택 — 기본값으로 이미 동작
```

**Codex CLI** (같은 훅; 호환성 실측, 실전 미검증):
```
codex plugin marketplace add https://github.com/jungzuna/haejwo
codex plugin add haejwo@haejwo
```
`/hooks`로 훅을 한 번 신뢰하세요. 명령어는 `@haejwo-*` 스킬입니다.

설치 후 세션을 재시작하세요(훅은 시작 때 로드). 로컬 설치: 클론한 뒤 `/plugin marketplace add <클론 경로>` (codex도 동일).

## 무엇을 얻나

**판단은 비싸게.** 호스트는 언제나 **세션에서 고른 그 모델**(바꾸지 않음)이고 계획·결정·검토를 맡으며, feature급 작업은 리뷰어 비평을 거친 계획에서 출발합니다(규범: 마커는 관찰만; 리뷰어가 꺼지면 같은 모델이 비평). `PreToolUse` 훅이 메인 에이전트의 **턴당 코드파일 N개**(기본 2) 초과 편집을 거부하고(코드 확장자면 프로젝트 안팎 모두; 메타데이터 디렉터리·프로젝트 밖 임시 경로는 면제) Bash 코드 수정은 휴리스틱으로 막습니다. 서브에이전트는 면제, 훅 오류는 통과(fail-open). 게이트는 보안 경계가 아닌 편의 장치(P4)로, 코드 파일이 아닌 `config.json`을 고치면 모델도 사용자도 끌 수 있습니다.

**실행은 설정된 티어로.** 구현과 잡무는 워커 티어로: Claude Code에선 `default-worker`(Opus, high effort), `task-worker`(Opus, low), 선택형 `Budget`(sonnet/haiku); Codex에선 `spawn_agent` 모델 매핑(기본은 호스트 모델 상속). 비용 절감은 측정 후 정한 더 싼 티어에서만.

**리뷰는 다른 회사 모델이.** `/haejwo:setup`으로 켜고 검증하면(기본은 꺼짐) 리뷰어는 상대 회사 모델입니다 — Claude Code에선 codex, Codex에선 claude. 그 CLI나 검증이 없으면 같은 계열 `deep-reasoner`가 대신합니다(독립성 약함). 켜면 브리프와 리뷰어가 읽는 저장소 내용이 상대 회사로 전송됩니다([고지](haejwo/commands/setup.md)).

자세히: [`haejwo/README.md`](haejwo/README.md) · [`PHILOSOPHY.md`](haejwo/PHILOSOPHY.md) · [`PROMPTS.md`](haejwo/PROMPTS.md).

### 조합별로 얻는 것

| | Claude Code만 | Codex만 | 둘 다 |
| --- | --- | --- | --- |
| 게이트·규칙·plan-first·push ask-first | ✓ | ✓ | ✓ |
| 위임 게이트 (범용 에이전트는 모델 명시 필수) | ✓ | — (`spawn_agent` 미연결) | ✓ Claude Code 쪽 |
| 모델 티어 (판단은 상속) | ✓ 세션 모델/opus/opus | ✓ `spawn_agent` 매핑 | ✓ |
| **교차-벤더 리뷰** | 대체: `deep-reasoner` | 대체: 같은 모델 서브에이전트 | ✓ codex↔claude, setup 검증 후 |

## 명령어 (설정·점검 전용)

평소엔 **하나도** 필요 없습니다: `/haejwo:setup`(1회 설정) · `/haejwo:status`(읽기 전용 상태) · `/haejwo:gate`(예산 `N`, `on`/`off`) · `/haejwo:plan`(수동 트리거).

## 하지 않는 것

haejwo를 하네스가 아닌 윤활층으로 두는 경계:

- 스케줄러, 영속 작업 큐, 상주 에이전트 로스터
- 범용 DAG나 재귀적 멀티에이전트 런타임
- 모델 게이트웨이, 과금 최적화, 가격 기반 라우터
- 교차-벤더 **워커** 라우팅(워커는 호스트 벤더를 따름; GPT 실행은 Codex 호스트로). `codex@openai-codex`와 공존하되 리뷰는 haejwo의 비편집 러너로
- 워커용 worktree 오케스트레이션이나 패치 병합
- 호스팅 컨트롤 플레인이나 대시보드
- 자율 push/배포/공개
- 워크플로 DSL이나 온톨로지 프레임워크
- 두 번째 운영 아키텍처 (값싼 메인의 어드바이저 모드)
- 판단 사항의 하드 게이트(plan 마커, 보고 길이): 규범과 넛지만

## 설계상 의도

반복되는 반론에 대한 답([상세](haejwo/README.md#by-design)):

- 파일 예산은 같은 모델 워커에도 적용: 가격이 아니라 호스트 컨텍스트를 아낍니다.
- 예산은 성공이 아닌 허용된 시도를 셉니다: 훅 하나로 단순하게.
- 러너는 `enabled`/`verified_at`을 보지 않습니다: 정책은 호스트, 러너는 도구.
- 저장된 `danger-full-access` 동의는 유지되고 실행마다 헤더에 표시됩니다.
- 오래된 캐시 경로 호출은 설치 버전으로 넘깁니다: 재시작 요구는 실측된 호스트 결함을 사용자에게 떠넘기고, 지원 하한이 비용을 묶습니다.
- 러너의 산출물 보호·변경 감지는 유지: 단순 status/diff엔 그 계약이 없습니다.
- bash 가드는 리다이렉트·`tee`·제자리 편집기만: `cp`/`mv`/`patch` 휴리스틱과 확장자 추가는 오탐 비용 때문에 안 넣습니다.
- Fable 호스트의 Explore는 `opus` 별칭(Claude Code 문서)이라 모델 명시가 비용을 올리지 않습니다.

## 검증

`python3 tests/test_hooks.py` — 계약 테스트 스위트(표준 라이브러리만; bash, git, Unix 호스트, 두 테스트는 이 저장소의 git 이력 필요). 커밋은 **파이프 없는** 종료 코드로 판정하세요.

변경 내역: [Releases](https://github.com/jungzuna/haejwo/releases) · [tags](https://github.com/jungzuna/haejwo/tags).

## 라이선스

[Apache-2.0](LICENSE)
