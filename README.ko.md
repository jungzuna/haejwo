<p align="center">
  <img src="assets/haejwo.png" width="520" alt="해줘 — 비싼 모델은 드러누워 해줘만 외치고, 작은 워커들이 땀 흘리며 실제 일을 한다">
</p>

<h1 align="center">해줘</h1>

<p align="center"><strong>haejwo — "just handle it."</strong><br><em>당신은 말만 하세요. 나머지는 모델들이 알아서 합니다.</em></p>

<p align="center"><sub><a href="README.md">English</a></sub></p>

[Claude Code](https://claude.com/claude-code)와 [Codex](https://github.com/openai/codex)는 이미 공식 코딩 하네스입니다 — 완성도, 사용자 규모, 자기 모델과의 궁합 모두 가장 앞서 있죠. haejwo는 이들을 대체하려는 물건이 아닙니다. **깔면 그걸로 끝** — 설정 한 줄, 외울 명령어 하나 없이, 그 위에서 **여러 모델이 알아서 잘 굴러가게** 만드는 콜드스타트 플러그인입니다. 원하는 걸 프롬프트로 적기만 하면 — 아무리 대충 적어도, 그게 바로 "해줘" — 호스트 모델이 계획을 세우고, 비용에 맞는 티어로 일을 나누고, (상대 CLI가 있다면) **다른 회사 모델**과 토론을 거쳐 검토·검증까지 마칩니다.

핵심 아이디어는 하나입니다: 비싼 메인 모델은 **판단**(계획·위임·결정·종합)에만 쓰고 **실행**은 싼 티어로 내려보낸다 — 그리고 그걸 말로만 부탁하지 않습니다. 메인 에이전트가 위임 대신 직접 구현을 시작하는 순간, `PreToolUse` 훅이 **물리적으로 막습니다**.

## 설치

**Claude Code:**
```
/plugin marketplace add jungzuna/haejwo
/plugin install haejwo@haejwo
```

**Codex CLI** (같은 repo, 같은 훅 — 전부 실측으로 확인된 호환):
```
codex plugin marketplace add https://github.com/jungzuna/haejwo
codex plugin add haejwo@haejwo
```
대화형 codex에서는 `/hooks`로 훅을 한 번만 신뢰해 주세요. 명령어는 `@haejwo-*` 스킬로 나타납니다.

**Codex가 없어도 됩니다** — Claude Code 호스트에 codex가 없으면 리뷰는 번들된 `deep-reasoner`가 대신합니다(같은 계열이라 독립성은 한 단계 약해집니다). **Opus 접근이 없다면** `/haejwo:setup`에서 `Balanced`나 `Budget` 프리셋을 고르세요 — 모든 역할이 계정에 실제로 있는 모델 안에서 돕니다.

훅은 세션이 시작될 때 로드됩니다 — 설치 후 재시작하세요(Claude Code는 `/reload-plugins`). 첫 실행 때 딱 한 번 `/haejwo:setup`을 권합니다: 모델 티어·편집 예산·리뷰어를 선택지로 답하면 영구 저장되고, 다시 묻지 않습니다. 저장하기 전에도 안전한 기본값으로 이미 돌아갑니다.

로컬 개발용: 클론한 뒤 `/plugin marketplace add <클론 경로>` / `codex plugin marketplace add <클론 경로>`.

## 무엇을 얻나

| 기능 | 하는 일 |
| --- | --- |
| **무설정 오케스트레이션** | 세션이 시작되면 규칙과 현재 설정이 자동으로 주입됩니다. 안전 기본값 즉시 가동 — 게이트 ON, 턴당 2파일, bash-guard ON |
| **판단-우선 계획** | feature급 작업은 코드보다 합의가 먼저 — 기획·분석·검토의 결정들을 리뷰어와 토론해 정리한 뒤에야 구현으로 넘어갑니다 |
| **교차-벤더 리뷰** (가능할 때) | 두 CLI가 다 있으면 리뷰어는 언제나 상대 회사의 모델 — Claude Code에선 codex가, Codex에선 claude가 검토합니다 |
| **싼 실행 티어** | 판단은 호스트가 맡습니다 — 호스트는 **세션에서 고른 그 모델 그대로**이며, haejwo가 절대 바꾸지 않습니다. 구현과 잡무만 저렴한 워커 티어로 내려갑니다(Codex에선 `spawn_agent` 모델 매핑) |
| **물리적 위임 게이트** | PreToolUse 게이트가 턴당 **코드파일 N개**를 넘는 편집과 메인 에이전트의 Bash 코드 수정을 거부합니다. 서브에이전트는 면제, 훅 오류는 무조건 통과(fail-open) |
| **push 동의** | 워커는 push/배포를 절대 하지 않습니다. 호스트도 `/haejwo:push auto`로 허락받기 전엔 매번 물어봅니다 |

평소엔 **해줘 명령어를 칠 일이 없습니다** — 명령어는 설정·점검용(`setup`, `status`, `gate`, `push`, 그리고 수동 트리거용 `plan`)이 전부입니다.

### 조합별로 얻는 것

| | Claude Code만 | Codex만 | 둘 다 |
| --- | --- | --- | --- |
| 게이트 + 규칙 + plan-first + push 동의 | ✓ | ✓ | ✓ |
| 모델 티어 (실행은 싸게, 판단은 비싸게) | ✓ opus/sonnet/haiku | ✓ `spawn_agent` 모델 매핑 (판단은 상속, 실행만 다운시프트) | ✓ |
| **교차-벤더 적대적 리뷰** | 대체: 같은 계열 `deep-reasoner` | 대체: 같은 모델 서브에이전트 (독립성 약함) | ✓ codex↔claude |

다른 쪽 CLI는 **다른 회사 모델의 리뷰**가 필요할 때만 설치하면 됩니다 — 두 번째 CLI가 사주는 게 정확히 그것이고, Claude Code를 추가하면 모델 티어도 따라옵니다. 같은 모델끼리의 대체 리뷰도 돌긴 하지만, 자기 검토가 놓치는 걸 잡는 건 결국 다른 모델입니다.

## 명령어 (설정·점검 전용 — 조연입니다)

평소엔 하나도 필요 없습니다. 하네스를 조정하거나 들여다볼 때만:

| Claude Code · Codex 스킬 | 역할 |
| --- | --- |
| `/haejwo:setup` · `@haejwo-setup` | 최초 1회 설정 — 티어·편집 예산·bash-guard·리뷰어. 한 번 답하면 영구 저장 |
| `/haejwo:status` · `@haejwo-status` | 현재 설정, 이번 턴 편집 카운터, 리뷰어 상태, 훅 관찰 기록과 이상징후 |
| `/haejwo:gate` · `@haejwo-gate` | 게이트 실시간 조정 — 예산 `N`, `on`/`off` (비상용) |
| `/haejwo:push` · `@haejwo-push` | repo별 push 동의 — 허락하기 전까진 매번 물어봅니다 |
| `/haejwo:plan` · `@haejwo-plan` | plan 합의 수동 트리거 (호스트가 어차피 알아서 돌립니다) |

## 듀얼호스트 패리티

repo 하나, `hooks.json` 하나, python 코어 하나 — codex 쪽 동작은 전부 **추측이 아니라 실측**으로 확인했습니다(환경변수 호환 별칭, deny 왕복, `apply_patch` 멀티파일 파싱과 통째 거부, `turn_id` 턴 리셋, 서브에이전트 `agent_type` 면제). codex 쪽 티어는 네이티브 `spawn_agent`의 model/effort 파라미터로 돕니다 — 판단(reasoner) 티어는 호스트 모델을 그대로 물려받아 **판단이 조용히 다운그레이드되는 일은 없고**, 실행·잡무 티어만 내려갑니다.

## 문서

| 문서 | 내용 |
| --- | --- |
| [`haejwo/README.md`](haejwo/README.md) | 딥다이브: 게이트 동작 원리, 첫 실행, 명령어, 추론 정책, 검증 |
| [`haejwo/PHILOSOPHY.md`](haejwo/PHILOSOPHY.md) | 헌법 — 원칙 12개와 그 유래, 충돌할 때의 우선순위, 개정 규칙 |
| [`haejwo/PROMPTS.md`](haejwo/PROMPTS.md) | LLM이 읽고 쓰는 모든 문구의 스타일 규정 (deny 문구는 테스트로 보증되는 계약) |

## 검증

`python3 tests/test_hooks.py` — 외부 의존성 없는(stdlib만) 계약 테스트 스위트. push마다 CI가 돌립니다.

## 라이선스

[Apache-2.0](LICENSE)
