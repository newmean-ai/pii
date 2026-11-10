# 기여 안내

도와주셔서 고맙습니다. newmean-pii는 [newmean.ai](https://newmean.ai)에서 한 사람이 관리합니다. 그래서 아래 규칙은
단순하고 예측할 수 있게 정했습니다. [English](CONTRIBUTING.md)

## 응답과 배포 주기

- 모든 Issue와 Pull request에 **일주일 안에** 첫 답을 드립니다.
- 배포는 **일주일에 한 번**, 그 주에 받아들인 변경을 묶어서 합니다.
- 긴급 수정(보안, 또는 개인정보가 새어 나갈 수 있는 잘못된 결과)은 **48시간 안에** 배포합니다.

## Issue

- **버그**: 버그 템플릿을 써 주세요. 패키지와 버전(npm, PyPI, Swift, Android, Flutter), 모델 크기, 문제를 보여 주는
  짧은 입력, 기대한 결과를 적어 주세요. 실제 개인정보는 지어낸 값으로 바꿔 주세요.
- **기능 요청**: 환영합니다. 다만 범위를 일부러 작게 유지합니다. 범위 밖이면 짧은 이유와 함께 닫을 수 있고, 아이디어가
  나쁘다는 뜻은 아닙니다.
- **보안 문제**: 공개 Issue에 올리지 마세요. [SECURITY.md](SECURITY.md)를 봐 주세요.

## Pull request

이 공개 저장소는 비공개 개발 저장소에서 만들어 올리므로, **Pull request를 GitHub 버튼으로 merge하지 않습니다**. 대신

1. 변경을 받아들이면 관리자가 개발 저장소에 옮겨 넣고, 커밋에 `Co-authored-by: <기여자>`로 공로를 남깁니다.
2. 다음 주간 배포에 들어가고, Pull request는 그 릴리스 링크와 함께 닫습니다.

작은 수정보다 큰 변경은 먼저 Issue를 열어 방향을 맞춘 뒤 작업해 주세요.

확인할 것:

- Pull request 하나에 변경 하나, 동작이 바뀌면 테스트도 함께.
- 고친 패키지의 테스트를 돌려 주세요.

  | 패키지 | 명령 |
  |---|---|
  | `js/` | `npm ci && npm test` |
  | `native/core/` | `make test` |
  | `python/` | [python/README.ko.md](python/README.ko.md) 참고(`uv build` 뒤 `pytest -q`) |
  | `swift/` | `swift test`(저장소 최상위) |
  | `android/` | `./gradlew :pii:testDebugUnitTest` |
  | `flutter/` | `dart test` |

- `native/core/`의 C 코어는 Python, Swift, Android, Flutter 패키지가 함께 씁니다. 여기를 바꾸면 모두가 npm 패키지와
  같은 결과를 내야 합니다.
- 학습 데이터와 데이터 생성기는 이 저장소에 없어서, Pull request로 모델을 바꿀 수는 없습니다.

## 기여물의 라이선스

기여물을 보내면 프로젝트와 같은 [Apache License 2.0](LICENSE)으로 제공하는 데 동의하고(Apache-2.0 제5조), 보낼 권리가
있다고 확인하는 것입니다.

## 행동 규칙

친절하고 구체적으로 말해 주세요. 괴롭힘이나 인신공격은 프로젝트 공간에서 내보내는 사유가 됩니다.
