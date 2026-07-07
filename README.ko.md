<p align="center">
  <img src="assets/haejwo.png" width="520" alt="해줘 — 비싼 모델은 드러누워 해줘만 외치고, 작은 워커 티어들이 땀 흘리며 실제 작업을 한다">
</p>

<h1 align="center">해줘</h1>

<p align="center"><strong>haejwo — "just handle it."</strong><br><em>말만 하세요; 모델들이 자기들끼리 알아서 굴립니다.</em></p>

<p align="center"><sub><a href="README.md">English</a></sub></p>

[Claude Code](https://claude.com/claude-code)와 [Codex](https://github.com/openai/codex)는 이미 공식 코딩 하네스입니다 — 가장 완성돼 있고, 가장 많이 쓰이고, 자기 모델과 가장 잘 맞습니다. haejwo는 이들을 대체하지 않습니다 — **깔면 그걸로 끝**: 설정도 워크플로 명령어도 없이, 그 위에서 **여러 모델이 알아서 잘 굴러가게** 만드는 콜드스타트 플러그인입니다. 그냥 원하는 걸 프롬프트로 쓰면 — 아무리 대충 써도, 그게 바로 "해줘" — 호스트 모델이 계획하고, 비용 티어로 위임하고, (상대 CLI가 있으면) **다른 회사 모델**을 리뷰어 삼아 토론하고, 검토·검증합니다.

핵심 아이디어: 비싼 메인 모델은 **판단**(계획·위임·결정·종합)만 하고 **실행**은 싼 티어로 — 그리고 부탁만 하는 게 아니라, 메인 에이전트가 위임 대신 직접 구현을 시작하면 `PreToolUse` 훅이 **물리적으로 차단**합니다.

## 설치

**Claude Code:**
```
/plugin marketplace add jungzuna/haejwo
/plugin install haejwo@haejwo
```

**Codex CLI** (같은 repo, 같은 훅 — 실측 호환):
```
codex plugin marketplace add https://github.com/jungzuna/haejwo
codex plugin add haejwo@haejwo
```
대화형 codex에서 `/hooks`로 훅을 1회 신뢰해 주세요. 명령어는 `@haejwo-*` 스킬로 나타납니다.

**Codex는 선택입니다** — Claude Code 호스트에서 codex가 없으면 리뷰는 번들된 `deep-reasoner`로 대체됩니다 (같은 계열이라 독립성은 약해집니다). **Opus 접근이 없다면?** `/haejwo:setup`에서 `Balanced`/`Budget` 프리셋을 고르세요 — 모든 역할이 계정이 실제로 가진 모델 안에서 돕니다.

훅은 세션 시작 시 로드됩니다 — 설치 후 재시작(Claude Code는 `/reload-plugins`). 첫 실행 때 `/haejwo:setup`(모델 티어·예산·리뷰어 대화형 설정, 1회 저장)을 한 번 권하고 다시 묻지 않습니다. 설정 전에도 안전 기본값이 켜져 있습니다.

로컬 개발 설치: 클론 후 `/plugin marketplace add <클론경로>` / `codex plugin marketplace add <클론경로>`.

## 무엇을 얻나

| 기능 | 하는 일 |
| --- | --- |
| **무설정 오케스트레이션** | SessionStart가 규칙과 현재 설정을 자동 주입. 안전 기본값 즉시 가동: 게이트 ON, 2파일/턴, bash-guard ON |
| **판단-우선 계획** | feature급 작업은 plan 합의로 시작 — 기획·분석·검토 결정을 구현 전에 토론 |
| **교차-벤더 리뷰** (가능할 때) | 두 CLI가 있으면 리뷰어는 다른 회사 모델 — Claude Code에선 codex, Codex에선 claude |
| **싼 실행 티어** | 판단은 호스트가 — 그리고 호스트는 **세션에서 지정한 그 모델 그대로**, haejwo가 절대 바꾸지 않습니다. 구현·잡무만 저렴한 워커 티어로 (Codex에선 `spawn_agent` 모델 매핑) |
| **물리적 위임 게이트** | PreToolUse 게이트가 턴당 **코드파일 N개** 초과와 메인 에이전트의 Bash 코드 수정을 차단. 서브에이전트 면제, 훅 오류는 통과(fail-open) |
| **push 동의** | 워커는 절대 push/배포 안 함. 호스트도 `/haejwo:push auto`로 허락하기 전엔 물어봄 |

평상시엔 **해줘 명령어를 한 번도 안 칩니다** — 명령어는 설정·점검용(`setup`, `status`, `gate`, `push`, 수동 트리거용 `plan`)뿐입니다.

### 조합별로 얻는 것

| | Claude Code만 | Codex만 | 둘 다 |
| --- | --- | --- | --- |
| 게이트 + 규칙 + plan-first + push 동의 | ✓ | ✓ | ✓ |
| 모델 티어 (실행은 싸게, 판단은 비싸게) | ✓ opus/sonnet/haiku | ✓ `spawn_agent` 모델 매핑 (판단은 상속, 실행은 다운시프트) | ✓ |
| **교차-벤더 적대적 리뷰** | 대체: 같은 계열 `deep-reasoner` | 대체: 같은 모델 서브에이전트(독립성 약함) | ✓ codex↔claude |

다른 쪽 CLI는 **다른 모델의 리뷰**를 원할 때만 설치하면 됩니다 — 두 번째 CLI가 사주는 게 바로 그것입니다(Claude Code를 추가하면 모델 티어도 함께). 같은 모델 대체도 동작하지만, 다른 모델은 자기검토가 못 잡는 걸 잡습니다.

## 명령어 (설정·점검 전용 — 보조 역할)

평상시엔 **하나도 필요 없습니다** — 그냥 말하면 됩니다. 하네스를 조정·점검할 때만:

| Claude Code · Codex 스킬 | 역할 |
| --- | --- |
| `/haejwo:setup` · `@haejwo-setup` | 첫 설정 — 티어·편집 예산·bash-guard·리뷰어. 한 번 물고 저장 |
| `/haejwo:status` · `@haejwo-status` | 현재 설정, 이번 턴 편집 카운터, 리뷰어 상태, 훅 관찰 기록 |
| `/haejwo:gate` · `@haejwo-gate` | 게이트 실시간 조정 — 예산 `N`, `on`/`off` (비상 해치) |
| `/haejwo:push` · `@haejwo-push` | repo별 push 동의 — 허락 전까지 ask-first |
| `/haejwo:plan` · `@haejwo-plan` | plan 합의 수동 트리거 (호스트가 어차피 알아서 돌립니다) |

## 듀얼호스트 패리티

repo 하나, `hooks.json` 하나, python 코어 하나 — codex 동작은 전부 **추측이 아니라 실측**했습니다(환경변수 호환 별칭, deny 왕복, `apply_patch` 멀티파일 파싱과 통째-거부, `turn_id` 턴 리셋, 서브에이전트 `agent_type` 면제). codex 쪽 티어는 네이티브 `spawn_agent`의 model/effort 파라미터로 돕니다 — 판단(reasoner) 티어는 호스트 모델을 상속(판단은 조용히 다운그레이드되지 않음), 실행·잡무 티어만 다운시프트.

## 문서

| 문서 | 내용 |
| --- | --- |
| [`haejwo/README.md`](haejwo/README.md) | 딥다이브: 게이트 시맨틱, 첫 실행, 명령어, 추론 정책, 검증 |
| [`haejwo/PHILOSOPHY.md`](haejwo/PHILOSOPHY.md) | 헌법 — 원칙 12개와 유래 사건, 충돌 시 우선순위, 개정 규칙 |
| [`haejwo/PROMPTS.md`](haejwo/PROMPTS.md) | 모든 LLM 대면 문자열의 스타일 법 (deny 문구는 테스트되는 계약) |

## 검증

`python3 tests/test_hooks.py` — hermetic 계약 테스트 스위트(stdlib만). CI가 push마다 실행합니다.

## 라이선스

[Apache-2.0](LICENSE)
