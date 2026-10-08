<p align="center">
  <img src="assets/haejwo.png" width="520" alt="해줘 — 비싼 모델은 드러누워 해줘만 외치고, 워커 티어들이 실제 일을 한다">
</p>

<h1 align="center">해줘</h1>

<p align="center"><strong>haejwo — "just handle it."</strong><br><em>당신은 말만 하세요. 나머지는 모델들이 알아서 합니다.</em></p>

<p align="center"><sub><a href="README.md">English</a></sub></p>

haejwo는 [Claude Code](https://claude.com/claude-code)와 [Codex](https://github.com/openai/codex)용 훅·규칙 플러그인입니다. 세션 시작 때 오케스트레이션 규칙을 주입하고, 메인 에이전트의 코드 편집이 턴당 예산을 넘으면 거부해 구현을 워커 서브에이전트로 넘기며, 켜 두면 계획을 상대 회사 모델에게 리뷰받습니다. 이미 두 하네스 중 하나를 쓰면서 세션 모델은 판단에만 쓰고 싶은 사람을 위한 것입니다.

원하는 걸 아무리 대충 적어도 — 그게 바로 "해줘" — 호스트가 계획하고, 위임하고, 검증합니다. 깔면 바로 켜지고, 설정도 워크플로 명령어도 필요 없습니다.

## 설치

`python3`가 필요합니다(CI는 3.10에서 테스트). 리뷰 러너는 Bash와 git도 사용합니다.

**Claude Code:**
```
/plugin marketplace add jungzuna/haejwo
/plugin install haejwo@haejwo
/reload-plugins   # 이미 열려 있는 세션이 있을 때만 (훅과 명령어를 갱신; 주입된 규칙은 재시작 필요)
/haejwo:setup     # 선택 — 기본값(게이트 ON, 턴당 2파일, bash-guard ON)으로 이미 동작합니다
```

**Codex CLI** (같은 repo, 같은 훅; 훅 호환성은 실측했지만 실제 프로젝트 작업에서는 아직 써보지 않았습니다):
```
codex plugin marketplace add https://github.com/jungzuna/haejwo
codex plugin add haejwo@haejwo
```
대화형 codex에서는 `/hooks`로 훅을 한 번만 신뢰해 주세요. 명령어는 `@haejwo-*` 스킬로 나타납니다.

훅은 세션 시작 때 로드되니 설치 후 세션을 재시작하세요. 로컬 개발용: 클론한 뒤 `/plugin marketplace add <클론 경로>` (codex도 동일).

## 무엇을 얻나

**판단은 비싸게.** 호스트는 언제나 **세션에서 고른 그 모델**이고, haejwo가 절대 바꾸지 않습니다. 계획·결정·검토는 호스트 몫이며, feature급 작업은 토론을 거친 계획에서 출발합니다. `PreToolUse` 훅이 메인 에이전트의 **턴당 코드파일 N개**(기본 2) 초과 편집을 거부하고 — 알려진 편집 도구, 지정된 코드 확장자, 프로젝트 안의 경로만 세며 프로젝트 밖 임시 파일은 면제 — Bash 코드 수정은 휴리스틱으로 막습니다. 서브에이전트는 면제, 훅 오류는 무조건 통과(fail-open).

**실행은 설정된 티어로.** 구현과 잡무는 설정된 워커 티어로 갑니다 — Claude Code 기준 `default-worker`는 세션 effort와 상관없이 Opus high effort, `task-worker`는 Opus low effort, 더 싸게 쓰려면 `Budget`(sonnet/haiku); Codex에서는 `spawn_agent` 모델 매핑. 기본값은 전부 Opus에 리뷰어 꺼짐이라, 비용 절감은 측정 후 직접 정한 더 싼 티어에서 나옵니다.

**리뷰는 다른 회사 모델이.** 두 CLI가 다 있고 `/haejwo:setup`으로 리뷰어를 켜고 검증하면(기본값은 꺼짐) 리뷰어는 상대 회사의 모델입니다 — Claude Code에선 codex가, Codex에선 claude가. 상대 CLI가 없거나 검증 전이면 같은 계열 `deep-reasoner`가 대신합니다(독립성은 약해집니다). 켜면 브리프와 리뷰어가 읽는 저장소 내용이 상대 회사로 전송됩니다([고지](haejwo/commands/setup.md)).

자세히: [`haejwo/README.md`](haejwo/README.md) · [`PHILOSOPHY.md`](haejwo/PHILOSOPHY.md) · [`PROMPTS.md`](haejwo/PROMPTS.md).

### 조합별로 얻는 것

| | Claude Code만 | Codex만 | 둘 다 |
| --- | --- | --- | --- |
| 게이트·규칙·plan-first·push ask-first | ✓ | ✓ | ✓ |
| 위임 게이트 (범용 에이전트는 모델 명시 필수) | ✓ | — (`spawn_agent` 미연결) | ✓ Claude Code 쪽 |
| 모델 티어 (판단은 상속) | ✓ 세션 모델/opus/opus | ✓ `spawn_agent` 매핑 | ✓ |
| **교차-벤더 리뷰** | 대체: `deep-reasoner` | 대체: 같은 모델 서브에이전트 | ✓ codex↔claude, setup 검증 후 |

## 명령어 (설정·점검 전용)

평소엔 **하나도** 필요 없습니다: `/haejwo:setup`(최초 1회 설정) · `/haejwo:status`(읽기 전용 상태) · `/haejwo:gate`(예산 `N`, `on`/`off`) · `/haejwo:plan`(수동 트리거; 호스트가 알아서 돌립니다). Codex에서는 `@haejwo-*` 스킬입니다.

## 하지 않는 것

haejwo를 하네스가 아니라 그 위의 윤활층으로 붙들어 두는 경계선:

- 스케줄러, 영속 작업 큐, 상주 에이전트 로스터
- 범용 DAG나 재귀적 멀티에이전트 런타임
- 모델 게이트웨이, 과금 최적화, 가격 기반 라우터
- 교차-벤더 **워커** 라우팅 — 워커 벤더는 호스트를 따릅니다(GPT로 실행하고 싶다면 Codex 호스트로). 공식 `codex@openai-codex` 플러그인과 공존은 가능하지만, 리뷰는 haejwo의 비편집 consult 러너를 그대로 씁니다
- 워커용 worktree 오케스트레이션이나 패치 병합
- 호스팅 컨트롤 플레인이나 대시보드
- 자율 push/배포/공개
- 워크플로 DSL이나 온톨로지 프레임워크
- 두 번째 운영 아키텍처 (예: 값싼 메인을 쓰는 어드바이저 모드)
- 판단 사항에 대한 하드 게이트 (plan 마커, 보고 길이) — 규범과 넛지만

## 검증

`python3 tests/test_hooks.py` — Python은 표준 라이브러리만 쓰지만 bash, git, Unix 호스트가 필요한 계약 테스트 스위트(두 테스트는 이 저장소의 git 이력이 필요). 커밋은 **파이프를 타지 않은** 종료 코드로 판정하세요. push마다 CI가 돌립니다.

변경 내역: [Releases](https://github.com/jungzuna/haejwo/releases) · [tags](https://github.com/jungzuna/haejwo/tags).

## 라이선스

[Apache-2.0](LICENSE)
