<p align="center">
  <img src="assets/haejwo.png" width="520" alt="해줘 — 드러누운 비싼 모델과 일하는 워커 티어">
</p>

<h1 align="center">해줘</h1>

<p align="center"><strong>haejwo — "just handle it."</strong><br><em>당신은 말만 하세요. 나머지는 모델들이 알아서 합니다.</em></p>

<p align="center"><sub><a href="README.md">English</a></sub></p>

haejwo는 [Claude Code](https://claude.com/claude-code)와 [Codex](https://github.com/openai/codex)용 훅·규칙 플러그인입니다. 세션 시작 때 규칙을 주입하고, 턴당 예산을 넘는 메인 에이전트의 코드 편집을 거부해 구현을 워커로 넘기며, 켜면 계획을 상대 회사 모델이 리뷰합니다. 세션 모델을 판단에만 쓰려는 두 하네스 사용자용입니다.

아무리 대충 적어도(그게 "해줘") 호스트가 계획·위임·검증합니다. 깔면 바로 켜짐; 설정은 선택(전까지 안내 반복).

## 설치

`python3` 필요(CI는 3.10). 리뷰 러너엔 Bash·git도 필요.

**Claude Code:**
```
/plugin marketplace add jungzuna/haejwo
/plugin install haejwo@haejwo
/reload-plugins   # 열린 세션이 있을 때만
/haejwo:setup     # 선택(기본값으로 이미 동작)
```

**Codex CLI** (같은 훅; 호환성 실측, 실전 미검증):
```
codex plugin marketplace add https://github.com/jungzuna/haejwo
codex plugin add haejwo@haejwo
```
`/hooks`로 훅을 한 번 신뢰하세요. 명령어는 `@haejwo-*` 스킬입니다.

`/reload-plugins`(Claude Code)는 열린 세션의 훅·명령어·에이전트를 갱신하지만 주입 규칙은 세션 시작 때만 로드되니, 설치·업데이트 후 재시작. 로컬: 클론 후 `/plugin marketplace add <경로>`(codex도 동일).

## 무엇을 얻나

**판단은 비싸게.** 호스트는 언제나 **세션 모델**로 계획·결정·검토를 맡고, feature급 작업은 리뷰어가 비평한 계획에서 출발(규범; 꺼진 리뷰어는 같은 모델이 대신). `PreToolUse` 훅이 메인 에이전트의 **턴당 코드파일 N개**(기본 2) 초과 편집을 거부하고(프로젝트 안팎의 코드 확장자; 메타데이터 디렉터리·프로젝트 밖 임시 경로 제외) Bash 코드 수정은 휴리스틱 차단. 서브에이전트는 면제, 훅 오류는 통과(fail-open).

**실행은 설정된 티어로.** 구현·잡무는 워커 티어: Claude Code에선 `default-worker`(Opus, high effort), `task-worker`(Opus, low), 선택형 `Budget`(sonnet/haiku); Codex에선 `spawn_agent` 모델 매핑(기본 호스트 모델 상속). 비용 절감은 측정 후 고른 싼 티어에서만.

**리뷰는 다른 회사 모델이.** `/haejwo:setup`으로 켜고 검증하면(기본 꺼짐) 리뷰어는 상대 회사 모델(Claude Code에선 codex, Codex에선 claude). CLI나 검증 없으면 같은 계열 `deep-reasoner`가 대신합니다(독립성 약함). 켜면 브리프와 리뷰어가 읽는 저장소 내용이 상대 회사로 전송됩니다([고지](haejwo/commands/setup.md)).

자세히: [`haejwo/README.md`](haejwo/README.md) · [`PHILOSOPHY.md`](haejwo/PHILOSOPHY.md) · [`PROMPTS.md`](haejwo/PROMPTS.md).

### 조합별로 얻는 것

| | Claude Code만 | Codex만 | 둘 다 |
| --- | --- | --- | --- |
| 게이트·규칙·plan-first·push ask-first | ✓ | ✓ | ✓ |
| 위임 게이트(범용 에이전트는 모델 명시 필수) | ✓ | — (`spawn_agent` 미연결) | ✓ Claude Code 쪽 |
| 모델 티어(판단은 상속) | ✓ 세션 모델/opus/opus | ✓ `spawn_agent` 매핑 | ✓ |
| **교차-벤더 리뷰** | 대체: `deep-reasoner` | 대체: 같은 모델 서브에이전트 | ✓ codex↔claude, setup 검증 후 |

## 명령어 (설정·점검 전용)

평소엔 **하나도** 불필요: `/haejwo:setup`(1회 설정) · `/haejwo:status`(읽기 전용 상태) · `/haejwo:gate`(예산 `N`, `on`/`off`) · `/haejwo:plan`(수동 트리거).

## 하지 않는 것

하네스가 아닌 윤활층:

- 스케줄러, 영속 작업 큐, 상주 에이전트 로스터
- 범용 DAG나 재귀적 멀티에이전트 런타임
- 모델 게이트웨이, 과금 최적화, 가격 기반 라우터
- 교차-벤더 **워커** 라우팅(워커는 호스트 벤더; GPT 실행은 Codex 호스트로). `codex@openai-codex`와 공존하되 리뷰는 haejwo의 비편집 러너로
- 워커용 worktree 오케스트레이션이나 패치 병합
- 호스팅 컨트롤 플레인이나 대시보드
- 자율 push/배포/공개
- 워크플로 DSL이나 온톨로지 프레임워크
- 두 번째 운영 아키텍처(값싼 메인의 어드바이저 모드)
- 판단 사항 하드 게이트(plan 마커, 보고 길이): 훅 게이트 없음. 마커 없는 워커는 한 번 묻고, 실행 가능한 계획·사유 없으면 blocked 보고

## 설계상 의도

반복되는 반론의 답([상세](haejwo/README.md#by-design)):

- 같은 모델 워커에도 호스트 예산 적용: 컨텍스트 절약용.
- 예산은 허용된 시도를 셈: 단순한 훅 하나.
- 러너는 `enabled`/`verified_at`을 안 봄: 정책은 호스트, 러너는 도구.
- 저장된 `danger-full-access` 동의는 유지·실행마다 헤더 표시. Claude 리뷰어는 테스트·grep용 Bash 유지, 감지된 저장소 변경은 실패(문서화된 예외 제외).
- 오래된 캐시 경로 호출은 설치 버전으로: 재시작 요구는 호스트 결함 떠넘기기.
- 러너의 산출물 보호·변경 감지 유지: status/diff엔 그 계약 없음.
- bash 가드는 리다이렉트·`tee`·제자리 편집기만: `cp`/`mv`/`patch` 휴리스틱과 확장자 추가는 오탐 비용으로 제외. 알려진 공백: 따옴표 리다이렉트 대상 허용, `tee`는 첫 대상만.
- Fable 호스트의 Explore는 `opus` 별칭(Claude Code 문서): 모델 명시해도 비용 동일.
- 위임 수단(Agent 도구/`spawn_agent`) 없는 세션은 설계 밖·감지 불가: 멈추고 다음 사용자 턴에 재개하거나 비상 해제.
- plan 마커 집계·이상 탐지는 소유자가 읽는 현장 관찰(P8).
- 문서 문장 고정 테스트가 콜드 감사 4회를 수정으로 바꿈(P7).

## 검증

`python3 tests/test_hooks.py`: 계약 테스트 스위트(표준 라이브러리만; bash·git·Unix 호스트; 두 테스트는 저장소 git 이력 필요). 커밋은 **파이프 없는** 종료 코드로 판정.

변경 내역: [Releases](https://github.com/jungzuna/haejwo/releases) · [tags](https://github.com/jungzuna/haejwo/tags).

## 라이선스

[Apache-2.0](LICENSE)
