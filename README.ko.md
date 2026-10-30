<p align="center">
  <img src="assets/haejwo.png" width="520" alt="해줘 — 드러누운 비싼 모델과 일하는 워커 티어">
</p>

<h1 align="center">해줘</h1>

<p align="center"><strong>haejwo — "just handle it."</strong><br><em>당신은 말만 하세요. 나머지는 모델들이 알아서 합니다.</em></p>

<p align="center"><sub><a href="README.md">English</a></sub></p>

haejwo는 [Claude Code](https://claude.com/claude-code)와 [Codex](https://github.com/openai/codex)용 훅·규칙 플러그인입니다. 세션 시작 때 규칙을 주입하고, 턴당 예산을 넘는 메인 에이전트의 코드 편집을 거부해 구현을 워커로 넘기며, 켜면 계획을 상대 회사 모델이 리뷰합니다. 아무리 대충 적어도(그게 "해줘") 호스트가 계획·위임·검증합니다. 깔면 바로 켜짐; 설정은 선택(전까지 안내 반복).

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
`/hooks`로 훅을 한 번 신뢰하세요. 명령어는 `@haejwo-*` 스킬입니다. 주입 규칙은 세션 시작 때만 로드되니 설치·업데이트 후 재시작.

## 무엇을 얻나

**판단은 비싸게.** 호스트는 언제나 세션 모델로 계획·결정·검토를 맡습니다. `PreToolUse` 훅이 메인 에이전트의 **턴당 코드파일 N개**(기본 2; 메타데이터 디렉터리·프로젝트 밖 임시 경로 제외) 초과 편집을 거부하고 Bash 코드 수정은 휴리스틱 차단. Claude Code에서 `git push`를 요청한 턴은 `Outward actions:` / `Final-diff review:` 두 줄로 끝나야 하며, 없으면 Stop 훅이 한 번 막습니다(있는지만 확인; 자세히: [Contract](haejwo/README.md#contract-234)). 서브에이전트는 면제, 훅 오류는 통과(fail-open).

**실행은 설정된 티어로.** 구현·잡무는 워커 티어(`default-worker`, `task-worker`, 선택형 `Budget`; Codex에선 `spawn_agent` 매핑). 비용 절감은 측정 후 고른 싼 티어에서만.

**리뷰는 다른 회사 모델이.** `/haejwo:setup`으로 켜고 검증하면(기본 꺼짐) Claude Code에선 codex, Codex에선 claude가 리뷰; 아니면 같은 계열 `deep-reasoner`가 대신(독립성 약함). 켜면 브리프와 리뷰어가 읽는 저장소 내용이 상대 회사로 전송됩니다([고지](haejwo/commands/setup.md)).

교차-벤더 리뷰는 두 CLI가 모두 필요하고, 위임 게이트·노트·Stop 검사는 Claude Code 전용입니다. 조합별 기능과 하지 않는 것: [docs/non-goals.md](docs/non-goals.md).

## 명령어

평소엔 **하나도** 불필요: `/haejwo:setup`(1회 설정) · `/haejwo:status`(읽기 전용) · `/haejwo:gate`(예산 `N`, `on`/`off`, `bash on|off`) · `/haejwo:plan`(기본은 호스트가 실행).

## 링크

[플러그인 README](haejwo/README.md)([Contract](haejwo/README.md#contract-234), [By design](haejwo/README.md#by-design)) · [현장 기록](docs/field/README.md) · [하지 않는 것](docs/non-goals.md) · [`PHILOSOPHY.md`](haejwo/PHILOSOPHY.md) · [`PROMPTS.md`](haejwo/PROMPTS.md) · [Releases](https://github.com/jungzuna/haejwo/releases)

테스트: `python3 tests/test_hooks.py`; 커밋은 **파이프 없는** 종료 코드로 판정.

## 라이선스

[Apache-2.0](LICENSE)
