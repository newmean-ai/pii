[English](REPORT.md) | 한국어

# 브라우저에서 검증 가능한 프라이버시: 프라이버시 수준을 갖춘 다국어 합성 PII 데이터와 2–20 MB 모델

**Claude Opus 5.5** (Anthropic) · **GPT-6.1 Sol** (OpenAI) · **Hoonjae Lee** (newmean.ai)

Contact: hoonjae@newmean.ai

*v1.0, 2026-10-31, GitHub에만 공개한다. 자세한 내용은 두 부록에 있다: [APPENDIX-methods.ko.md](APPENDIX-methods.ko.md)
(데이터 방법 전체, M1–M15)와 [APPENDIX-experiments.ko.md](APPENDIX-experiments.ko.md)(시범, 검수 이력, 이전 비용, 이전 10개
레이블 모델, 공개 테스트에서의 v1.0 모델, 브라우저 실행기, A–P).*

---

## 초록

설문 도구, 상담 채팅, AI 비서는 이름, 전화번호, 주소, 신분 번호가 자주 섞인 텍스트를 받는다. 탐지한 스팬(span)을
**브라우저에서, 텍스트를 보내기 전에** 마스킹하면 그 부분은 서버에 닿지 않는다 — 탐지기가 놓친 부분은 그대로 전송된다 —
하지만 이를 위해서는 내려받을 만큼 작고, 휴대폰에서 돌릴 만큼 빠르고, 여러 언어를 다루는 탐지기가 필요하다. 공개 탐지기는
크거나, 영어와 라틴 문자 데이터 중심이다. 이 보고서는 데이터 v1.0을 설명한다. 54개 언어로 된 합성 텍스트
1,156,505행(학습 1.07M행과 검증셋, 테스트셋 두 개)으로, 국가별 식별번호 형식, 현실적인 이름과 외국인 이름, 약 75개 하위 유형(subtype), 두 가지 **PII 수준**(직접
식별자와 간접 식별자를 나누는 마스킹 정책)을 갖추었다. 세 Claude 모델로 생성했으며 모든 비용을 보고한다. 두 모델 계열이 무작위
검증 행 300개를 블라인드로 검수한 결과, 학습에 들어가는 행 중 2.7%에서 두 검수자가 모두 레이블을 바꿨다. 두 비공개 교사에서 증류한 2.6–15.5 MB
브라우저용 학생 모델 네 개는 우리 합성 데이터 newmean-pii-dataset의 검수를 마친 54개 언어 공개 테스트에서 PII 문자의 1.6–3.3%를 놓쳤다. 공개 탐지기 여덟 개와 정규식
기준선은 19.6–75.3%를 놓쳤다(대부분 다른 레이블 정책을 따른다). 우리 모델이 한 번도 보지 않은 한국어 KDPII 코퍼스에서는
7.6–9.8%를 놓쳤다. 모델, SDK, 그리고 리더보드가
딸린 newmean-pii-dataset의 공개 테스트를 공개했으며, 학습 데이터는 요청 시 제공한다.

---

## 1. 서론

개인정보는 늘 웹 양식과 채팅 창으로 들어온다. 설문 응답자가 동료를 이름으로 언급하고, 고객이 상담 채팅에 은행 계좌를 붙여
넣고, 사용자가 API 키가 든 설정 파일을 AI 비서에 붙여 넣는다. 텍스트가 서버에 도착하면 로그에 남고, 캐시되고, 제3자가
처리한다. 텍스트를 **기기에서, 보내기 전에** 마스킹하면 마스킹된 부분은 서버에 닿지 않는다. 탐지기가 놓친 부분은 그대로
전송된다. 이것이 newmean.ai 연구 설문 도구의 설계 원칙이다. 이 도구는 응답자의 브라우저에서 탐지한 개인정보를 마스킹한 뒤에야
답변을 제출한다. 이 보고서의 모델이 쓰일 SDK(`@newmean-ai/pii`)도 같은 원칙을 따른다.

브라우저 쪽 탐지기는 **작아야** 하고(모바일 연결로 수 메가바이트), **빨라야** 하며(휴대폰에서 메시지당 수십 밀리초),
**다국어·다영역**이어야 한다(설문 응답, 인사 양식, 의료 기록, 채팅, 로그, OCR 텍스트). 기존 공개 탐지기는 셋 중 많아야 둘을
충족하고, 공개 PII 데이터셋은 대부분 영어나 소수의 유럽 언어다(2절).

병목은 데이터다. 실제 개인정보는 학습용으로 수집·재배포할 수 없고, 54개 언어에 걸친 사람의 주석 작업은 느리고 비싸다. 그래서
우리는 학습 데이터를 대규모 언어 모델로 생성하되, 생성을 측정 가능한 실패 유형이 있는 공학 문제로 다룬다. 생성기는 예시 값을
베끼고, 영어로 흘러가고, 좋아하는 이름을 반복해 쓰고, 국가 형식을 무시하고, 가끔 레이블을 잘못 붙인다. 이 각각을 측정하고
대응했다(4절, 부록 M).

**기여.**
1. 다국어 PII 탐지를 위한 합성 데이터 방법: 79개 로케일의 국가별 식별번호 형식, 외국인 거주자 이름을 포함한 Wikidata 이름 풀,
   명시적인 설계 축, 약 75개 하위 유형과 두 PII 수준.
2. 품질 관리 파이프라인 — 자동 점검 관문(automatic gates), 두 모델 계열의 블라인드 이중 검수(blind double review), 중복 제거와
   누출 분석 — 과 측정한 변경률, 그리고 모든 생성 비용.
3. 네 등급(tier; nano 2.6 MB, small 7.1 MB, medium 11.3 MB, large 15.5 MB)의 브라우저용 학생 모델. 두 비공개 교사 모델에서
   증류했고, 밝힌 조건 아래에서 공개 탐지기와 비교했으며, 브라우저에서 실행하는 SDK와 함께 공개한다.
4. newmean-pii-dataset: 학습·검증 데이터, 54개 언어의 검수된 공개 테스트(17,058행)와 같은 설계의 비공개 테스트. 리더보드가 두 셋의 점수를 모두 보여 준다.

---

## 2. 관련 연구

모든 수치를 담은 더 자세한 판은 부록 M14에 있다.

**PII 탐지 시스템.** Microsoft Presidio는 개체명 인식(NER), 정규식, 규칙, 체크섬을 결합하지만 [1] 기본으로는 영어 인식기만
들어 있고, 다른 언어에는 별도의 모델과 문맥 단어가 필요하다 [2]. GLiNER는 레이블로 주어진 개체 유형을 추출한다 [3]. 그 PII
미세조정 모델 [4, 7]과 GLiNER2 [5] 파생 모델 가운데 GLiNER2-PII(205M 파라미터)는 7개 유럽 언어를 다루며 비유럽 문자에서는
측정하지 않았다 [6]. OpenAI의 Privacy Filter(1.5B 파라미터, 활성 50M)는 "주로 영어"이고 [8], 독립 평가는 페르시아어와 우크라이나어
일반 NER 세트에서 F1이 0.04 미만임을 보인다 [9]. 브라우저 크기의 모델 — Mozilla의 smart-pii-redactor(4비트에서 14.8 MB) [10],
Rampart(14.7 MB) [11], Rampart Edge(1.45 MB) [12] — 는 라틴 문자 언어만 다루거나 지원 언어를 밝히지 않는다. Horizon의
pii-redactor-small(141M) [13]은 OpenPII 검증셋에서 개체 F1 0.971을 보고하지만 외부 벤치마크에서는 가림(redaction) F1이 0.351이다.
상용 API는 국가별 유형을 많이 제공하거나 [14] 언어가 적고 [15], 최근 벤치마크들은 집계 점수가 실패를 가린다는 것을 보여 준다
[16, 17, 18]. 우리가 찾은 공개 모델 가운데 20 MB 미만인 것은 라틴 문자 전용이거나 지원 언어를 밝히지 않았고, 더 넓은 다국어
모델은 파라미터가 100M 이상이다.

**PII 데이터셋.** 공개 합성 데이터셋 [19, 20, 21, 22, 23, 24]은 대부분 영어나 유럽 언어다. KDPII는 한국어 대화다 [25, 26]. 오래된
다국어 NER 코퍼스는 PII가 아니라 일반 개체를 주석하고 [27, 28], 비식별화 코퍼스는 영어이며 특정 영역에 한정된다 [29, 30]. 우리
데이터는 언어 수(54), 국가별 식별번호 형식, 이름 풀, PII 수준에서 다르다.

**LLM이 생성한 학습 데이터.** LLM은 레이블링 비용을 줄이고 [31] NER 학습 데이터를 생성하거나 [32] 증류할 수 있지만 [33, 56],
단순한 프롬프트는 편향되고 다양성이 낮은 데이터를 만들며 속성 지정 프롬프트(attributed prompts)가 도움이 된다 [34]. 레이블이
주관적이면 합성 데이터의 도움이 적고 [35], LLM 레이블에는 잡음이 있다 [36]. 우리는 명시적인 설계 축을 쓰고, 레이블 오류는
블라인드 이중 검수로 측정한다.

**압축과 평가.** 우리 모델은 기존 기법을 결합한다 — 다국어 인코더 [37, 38, 39, 40, 41], 증류 [42, 43, 44], 어휘 축소 [45, 46],
4비트 양자화 [47, 48], ONNX Runtime Web [49]. 학습–테스트 중복과 처음 보는 개체는 점수에 영향을 주므로 [50, 51, 52, 53], 처음 보는
값을 가진 테스트 행은 따로 보고한다. 랜덤 시드만으로도 NER F1이 약 1점 움직인다 [54, 55]. 스팬 지표는 마스킹된 텍스트가 여전히
사람을 식별하는지 보여 주지 않는다 [57].

---

## 3. 과제와 레이블

**과제.** 텍스트가 주어지면, 사람을 식별하거나 식별을 돕는 문자 스팬을 레이블과 함께 표시한다. SDK는 모델의 스팬을
마스킹한다. 모델은 구조화된 유형(이메일, 카드, IBAN, IP 주소 등)도 학습하므로 패턴에 의존하지 않는다. SDK에는 선택해서 켤 수
있는 패턴 계층이 있지만 기본으로는 꺼져 있다. 공개 테스트에서 탐지 범위는 거의 넓히지 않고 과잉 마스킹만 두 배로 늘리기
때문이다(6절).

**레이블.** 모델 레이블은 13개다: PERSON, EMAIL, PHONE, ADDRESS, ID_NUMBER, FINANCIAL, ORG, USERNAME, URL, DATE_OF_BIRTH,
SECRET, 그리고 수준 2 스팬을 위한 PERSON_L2와 URL_L2(IP 주소는 ID_NUMBER로 학습한다). 데이터는 모든 스팬에 더 세밀한
**하위 유형**(예: ID_NUMBER:passport, FINANCIAL:iban; 총 약 75개)과, 가린 값이나 풀어 쓴 값 같은 표기 방식에 대한 플래그를 단다.
이것으로 선택적 마스킹이 가능하다. 모델은 레이블만 예측한다(부록 M1).

**PII 수준.** 전체 이름이나 여권 번호는 그 자체로 사람을 식별한다. 직함이 붙은 성("Kim, the manager"), 별명, 이니셜, 회사 공개
웹사이트 링크는 문맥이 있어야만 식별한다. 모든 스팬은 수준을 받으며, 수준은 하위 유형으로 정해진다. 수준은 마스킹 정책 — 사용자가
보이게 남기기로 선택할 수 있는 스팬 — 이지, 식별 가능성의 일반 분류가 아니다. 식별 가능성은 문맥에 달려 있다.

| 수준 | 스팬 |
|---|---|
| 1 — 직접 | 전체 이름, 이름(given name), 연락처, 모든 식별번호, 금융 데이터, 개인 URL, 비밀값, 생년월일, 주소, 개인이 속한 조직 |
| 2 — 간접 | PERSON: surname(성만 단독으로, 보통 직함이나 존칭과 함께), alias(이름처럼 쓰인 별명이나 애칭), initials; URL: public(사람을 가리키지 않는 링크) |

SDK는 기본으로 두 수준을 모두 마스킹하며, 수준 2를 보이게 둘 수 있다. 수준은 오류 분석에서 나왔다. 우리 이전 모델이 학습
실행마다 가장 불안정했던 스팬이 바로 별명과 성+직함 언급이었다(부록 I.3).

**지침.** 영어 지침 하나를 생성 프롬프트와 검수 프롬프트에 그대로 쓴다. 지침은 레이블별로 태그할 것, 스팬 경계(이름은 존칭과
문법적 어미를 뺀다), 겹침 규칙, 그리고 검수 중에 찾은 문화별 규칙을 정한다. 지침은 11개 버전을 거쳤다(부록 M1).

---

## 4. v1.0 데이터

생성기 코드는 공개하지 않는다(이해 상충 참조). 아래에 각 단계를 핵심 수치와 함께 요약한다. 부록 M은 코드 없이도 방법을 판단할
수 있을 만큼 자세히 설명한다.

### 4.1 생성

- **설계 축(design axes).** 호출 한 번은 모델 하나에게 한 언어·한 국가로 40개 항목을 요청한다. 계획기(planner)가 정확한
  할당량으로 국가(54개 언어에 79개 로케일), 16개 영역, 20개 형식(채팅, 이메일, 양식, CSV 행, 로그 줄, OCR 텍스트, …), 길이,
  화자(voice), PII 개수, 변형 요소(twist; 풀어 쓴 값, 코드 전환, 어려운 음성 예(hard negatives), …), 필수 초점 하위 유형을
  배분하므로, 드문 유형도 충분한 수만큼 나온다. 자세한 내용: M2.
- **PII 없는 항목.** 설계상 항목의 40%에는 PII가 없다. 실제 입력에는 PII가 없는 경우가 많고, 그런 글을 드물게 본 모델은 과잉
  마스킹하기 때문이다. 그중 53%가 자기만의 변형 요소를 받는다(닮은 숫자, 역할로만 불리는 사람, 자리표시자). M3.
- **국가 형식.** 로케일마다 공식 자료를 조사해 만든 형식 카드가 현지 식별번호의 모양을 알려 준다. 값은 체크섬이 아니라 형식에
  맞아야 하며, 이것이 실제 사람의 번호와 겹칠 가능성을 낮춘다. 형식 검사는 검사 가능한 검증 값의 0.5%에서 실패하고, 그런 행은
  학습에서 빠진다. M4.
- **이름.** 언어 모델은 몇 안 되는 이름을 반복해 쓰고, Faker [60]는 우리 로케일 중 일부만 다룬다. 79개 로케일 모두에 대해
  Wikidata [58] 이름 풀에서 뽑고, UN 이주 통계 [59]로 가중한 외국인 거주자 이름을 더한다. 다른 호출에 다시 나오는 전체 이름은
  언어별 전체 이름 스팬의 중앙값 1.4%다. M5.
- **생성기.** 세 Claude 모델이 데이터를 쓴다. 학습과 테스트는 서로 다른 생성기에서 나오므로, 벤치마크가 학습 생성기의 문체에
  보상하지 않는다.

  | 생성기 | v1.0에서의 역할 | 호출당 비용 (40행, Message Batches) |
  |---|---|---|
  | Claude Haiku 5.5, effort high | 학습·검증 데이터의 90% | $0.0104 |
  | Claude Sonnet 5.5, effort high | 학습·검증 데이터의 10%, 테스트셋의 50%, 테스트의 모든 SECRET 값 | $0.098 |
  | Claude Opus 5.5, effort high | 테스트셋의 50% | ≈ $0.2 |

  Opus는 비밀값 작성을 거부하므로 SECRET 항목은 Sonnet에 넘긴다. 항목은 인라인 태그가 붙은 채 돌아오고, 엄격한 파서가 이를
  문자 오프셋으로 바꾼다. M6.
- **대규모 생성.** 전체 라운드는 Message Batches API(50% 할인)를 쓴다. 행을 만드는 모든 입력을 해시로 고정하고, 모든 프롬프트와
  원본 응답을 저장하므로, 새 API 호출 없이 행을 다시 만들 수 있다(계획기가 검증셋 항목 명세 53,950개를 모두 정확히 재현한다). M7.
- **생성 뒤 값 바꾸기.** 자연스러운 값 풀이 없는 곳에서 생성기는 좋아하는 값으로 쏠린다(카드 유효기간 하나가 전체 유효기간
  스팬의 3분의 1 이상이었다). 유효기간, CVV, 일회용 인증 코드, 응답자 코드의 숫자는 행마다 결정적으로 바꾸고, 일반 날짜는 글과
  모순되지 않는 한(예: 날짜 옆의 요일) 함께 옮긴다. 그 뒤로는 바꾼 유형 어디에서도 한 값이 그 유형 스팬의 3.3%를 넘지 않는다. M8.

### 4.2 품질 관리

- **자동 점검 관문.** 모든 라운드에서 항목 명세(필수 하위 유형 포함 ≥ 95%), 형식 유효성(실패 ≤ 1%), 다른 언어 텍스트
  (≤ 0.5%), 예시 값 복사를 검사한다. 실패한 행은 해결될 때까지 학습에서 뺀다. M9.
- **블라인드 이중 검수.** 서로 다른 모델 계열의 두 검수자 — Claude Opus와 GPT-6.1 Sol — 가 생성된 레이블도 서로의 결과도 보지
  않고 원문만 보고 표본 행을 다시 태그한다. 두 검수자가 서로 일치하고 생성기와 다르면 그 행은 *변경*된 것으로 센다. 이는
  오류율이 아니라 합의된 변경의 비율이다. 두 검수자가 함께 저지른 오류는 놓치고, 잘못된 변경이 포함될 수 있다. 무작위 v1.0
  검증 행에서의 결과(표본 추출과 정의: M10):

  | 표본 | 두 검수자가 모두 바꾼 행 | 그중 관문이 이미 보류한 행 | 학습에 들어가는 행의 오류율 |
  |---|---|---|---|
  | 300행 (Haiku 90% / Sonnet 10%) | 10 (3.3%) | 2 | 8 / 298 (2.7%; 95% 구간 ≈ 1.4–5.2%) |
  | Sonnet 100행 | 5 (5%) | 3 (놓친 이름 셋 모두) | 2 / 97 (2.1%) |
  | 중국어 100행, GLM 대 생성기 | 경계 100%, 레이블과 수준 98.9%, 하위 유형 95.7% | | 생성기의 명백한 오류 1건 |

  중국어에는 GLM(Z.ai)이 참고용 세 번째 의견을 낸다.
- **공개 테스트 검수.** 17,058행 모두를 Claude Opus가 원문만 보고 다시 태그했다. Opus는 16,730행(98.1%)에서
  생성기 레이블을 그대로 재현했고, 이 행들은 독립된 두 태그가 일치하므로 통과시켰다. Opus가 다르게 태그한 328행은 GPT-6.1 Sol도
  원문만 보고 다시 태그했다(초기의 전체 실행에서 일치한 행 4,400개도 함께 봤다). 두 검수자가 같은 변경에 동의한 행이 220행(1.3%),
  다수결로 생성기 레이블이 유지된 행이 181행이다(328행 중 83행, 초기 전체 실행의 일치 행 중 98행). 남은 25행(세 버전이 모두 다르거나 검수자 한쪽이 답하지 않은 행)은 판정했다. ZCode의
  GLM이 버전 중 하나를 고르고, Claude가 그 선택을 지침에 비춰 하나씩 확인했다(23행 유지, 2행 변경). 하위 유형만 고친 행을 포함해
  모두 318행(1.9%)이 바뀌었다.
- **비공개 테스트 검수.** 같은 절차로 검수했다. 다만 남은 행은 GLM이 아니라 Claude가 판정하고 GPT가 확인해, 비공개 행이 이 두
  모델 제공자에게만 가게 했다. Opus는 17,001행 중 16,677행(98.1%)에서 생성기 레이블을 재현했다. GPT-6.1 Sol이 다시 태그한
  324행 중 두 검수자가 같은 변경에 동의한 행이 201행, 다수결로 생성기 레이블이 유지된 행이 93행, 남은 행이 30행이다. 남은 행 중
  20행은 Opus와 GPT의 판정이 일치했고, 나머지 10행은 Claude가 지침과 학습 데이터의 관례에 비춰 한 번 더 검토한 뒤 사람 저자가
  정했다. 모두 223행(1.3%)이 바뀌었다.
- **중복과 누출.** 완전 중복과 근사 중복 텍스트(마스킹한 단어 5-그램에 대한 MinHash, Jaccard ≥ 0.7)는 우선순위가 낮은 분할에서
  제거하며, 학습에서는 절대 제거하지 않는다. 검증셋과 첫 학습 샤드는 근사 중복 템플릿을 공유하지 않지만(완전 중복 35건은
  검증셋에서 제거), 값은 다시 나온다. 검증셋 PERSON 값의 44%와 전체 스팬의 23%가 학습 데이터에도 나온다(공개 테스트: 36%와
  20%). 그래서 PII 값이 모두 학습 데이터에 없는 공개 테스트 행(PII가 있는 행의 73%)도 따로 채점한다. 여기서 학생 모델은 PII
  문자의 1.5 / 2.0 / 2.6 / 3.2%를 놓쳐(large부터 nano 순) 전체 행의 1.6 / 2.1 / 2.7 / 3.3%보다 높지 않으므로, 다시 나오는 값이
  점수를 끌어올리지 않는다. M11.
- **다양성.** 고유 단어 3-그램 비율은 모든 학습 샤드에서 PII 없는 항목과 PII 항목 모두 약 0.95다(0.948–0.957과 0.953–0.961).
  M12.

### 4.3 예시

두 블라인드 검수자가 레이블을 정확히 그대로 재현한 v1.0 검증셋의 행이다. 인라인 태그는 데이터 형식이다(4.1절, M6). 값은 생성한 것이며
가상이 되도록 의도했다.

| 언어 · 형식 · 변형 요소 | 태그가 붙은 텍스트 |
|---|---|
| 한국어 · 설문 응답 | 월급 이번달에도 또 밀려서 ㅠㅠ 사장님한테 말했더니 그냥 기다리래요 ㅡㅡ 혹시 바로 보낼 수 있으면 `<FINANCIAL:bank_account>`농협 351-0268-5517-21`</FINANCIAL>` 로 부탁드려요. |
| 영어 · 설문 응답 · 여러 줄에 흩어진 PII | I help at the youth club on Thursdays.⏎my cousin gives me a lift in her silver Ford reg `<ID_NUMBER:vehicle_plate>`YW68 HLD`</ID_NUMBER>` cos the bus is rubbish lol |
| 스페인어 · 설문 응답 · 외국인 거주자 | Oye, la chica que nos llevó del aeropuerto se llama `<PERSON:full_name>`Najwa SEFRIOUI`</PERSON>` y en insta es `<USERNAME:handle>`@najwa_sf.tr`</USERNAME>`, si la buscáis ponedle follow xd. |
| 러시아어 · SMS | Уважаемая `<PERSON:surname>`Кузнецова`</PERSON>`, ваше заявление на соцвыплату одобрено, деньги поступят в течение 5 рабочих дней. (level 2) |
| 베트남어 · 그룹 채팅 | A: ai có pass tài khoản hệ thống kho ko⏎B: pass mới là `<SECRET:password>`Xk#82mLq1`</SECRET>`, đừng gửi lại trong group nha⏎A: ok, số zalo `<PHONE:mobile>`0377 508 216`</PHONE>` tui nhắn sau |
| 태국어 · CSV 행 · 어려운 음성 예 | ลำดับ,เลขผู้เสียภาษี,คะแนน,ความเห็น⏎12,`<ID_NUMBER:personal_tax>`3102847563019`</ID_NUMBER>`,5,ซื้อของที่ Lazada ทุกเดือน ชอบ Starbucks สาขาเชียงใหม่ honey ไม่ค่อยได้ใช้ระบบนี้เลยแต่ชอบมาก |
| 이탈리아어 · 양식 · PII 없음 (자리표시자) | Nome: [Nome] \| Email: {email} \| Matricola: [REDACTED] \| Reparto: *** \| Valutazione complessiva del processo di picking: 4/5, nessun commento. |
| 네덜란드어 · CSV 행 · PII 없음 (닮은 숫자) | 2024-03-12;4;3;5;bestelling 884512;factuur 2024-0931;€ 1.250,00;app v3.4.1;foutcode E-204;1488;ja;12:45 |

### 4.4 데이터셋

데이터 이름은 newmean-pii-dataset이고 학습, 검증, 공개 테스트, 비공개 테스트의 네 split으로 나뉜다. 이 보고서는
v1.0(태그 `data-v1.0`)을 다룬다. 검증셋과 학습 데이터의 첫 8분의 1은 이전 프롬프트 버전으로 생성하고 v1.0 값 바꾸기로
후처리했다. 최종 프롬프트와의 차이는 지침 몇 줄과 배정 규칙 하나뿐이다(M13).

| 분할 | 생성기 | 행 수(승인, 중복 제거 후) | 상태 |
|---|---|---|---|
| 학습 | Haiku 90% / Sonnet 10% | 1,069,002 | 완료 |
| 검증 | Haiku 90% / Sonnet 10% | 53,444 (언어당 ≈ 1,000) | 완료 |
| 공개 테스트 | Opus 50% / Sonnet 50% (SECRET 항목은 Sonnet만) | 17,058 (언어당 ≈ 320) | 모든 행 검수 완료(4.2절) |
| 비공개 테스트 | 같음, 별도 비공개 시드 | 17,001 | 모든 행 검수(4.2절), 리더보드 채점 작업만 사용(비공개) |

**분할을 만드는 방법.** 네 분할은 한 덩어리에서 잘라 낸 것이 아니다. 분할마다 별도의 호출로 생성하고 설계 축과 값 시드도 따로
쓴다. 두 테스트는 학습·검증과 다른 모델(주로 Haiku 대신 Opus와 Sonnet)이 만들었으므로, 테스트의 문장 틀이 학습 문장 틀과
같은 호출에서 나올 수 없다. 자동 검사를 통과하지 못한 행은 보류한다. 그다음 학습 → 검증 → 비공개 테스트 → 공개 테스트의 우선순위로
완전 중복과 유사 템플릿을 제거하며, 행은 언제나 우선순위가 낮은 쪽에서만 지운다(M11). 비공개 테스트를 공개 테스트보다 앞에
두므로, 비공개 행이 공개 테스트의 중복으로 공개되는 일은 없다. 제거된 행: 검증 107행, 공개 테스트 16행, 비공개 테스트 15행.

**행이란.** 행 하나는 사람이 입력하거나 붙여 넣거나 올리는 텍스트 한 건이다. 스프레드시트 셀 하나, 양식·설문 답변 하나, 채팅 대화
한 묶음, 이메일 한 통, 로그 한 줄, OCR 발췌 하나가 각각 한 행이며, 문장 하나를 뜻하지 않는다. 검증셋에서 행 길이의 중앙값은 167자
(10–90백분위 45–419자)이고, 32%는 여러 줄이며, 40%에는 PII가 없다. v1.0 전체는 1,156,505행, 2억 4천2백만 자, PII 스팬 109만 개다(학습 2억 2,350만 자와 스팬 1,007,176개, 검증 1,130만 자와
50,502개, 공개 테스트 360만 자와 17,147개, 비공개 테스트 360만 자와 17,275개).

모델은 3절의 13개 레이블을 학습한다. 수준 2 스팬은 학습 스팬의 7.7%다(M13).

### 4.5 데이터 v1.0의 비용

요청 단위 기록의 사용량을 API 정가로 계산했다. Message Batches는 50%, 실시간 보충 호출은 정가다(100만 토큰당 입력 / 출력 달러:
Haiku 5.5 0.10 / 0.50, Sonnet 5.5 2 / 10, Opus 5.5 4 / 20).

| 분할 | 행 수 | 요청 수 (실시간 보충) | 입력 토큰 | 출력 토큰 | 비용 |
|---|---|---|---|---|---|
| 학습 | 1,069,002 | 27,085 (86) | 365.6M | 1,115.8M | $564.61 |
| 검증 | 53,444 | 1,354 (4) | 18.1M | 56.6M | $28.18 |
| 공개 테스트 | 17,058 | 464 (11) | 6.2M | 8.5M | $73.24 |
| 비공개 테스트 | 17,001 | 457 (14) | 6.1M | 8.6M | $74.87 |
| **합계** | 1,156,505 | 29,360 (115) | 396.1M | 1,189.5M | **$740.91** |

Opus 요청은 Sonnet 요청의 약 두 배, Haiku 요청의 약 스무 배다. 그래서 두 테스트셋(Opus/Sonnet)은 요청의 3.1%이지만 비용은
전체의 20%다.

### 4.6 지금까지의 프로젝트 전체 비용 (API 요금 기준)

코딩 에이전트의 계획·구현부터 데이터 생성, 검수, 학습까지 프로젝트의 모든 모델 호출을 토큰으로 세고 API 정가로 계산했다. 실제로는
여러 작업이 구독 요금제로 돌았지만 요금제와 상관없이 같은 기준을 적용했다. 2026-10-29 기준이며, 테스트셋 검수와 학생 학습이 아직
진행 중일 때 집계했다. 모델·도구별 세부 내역은 부록 F에 있다.

| 단계 | 모델 / 자원 | 입력 (캐시 쓰기 포함) | 캐시 읽기 | 출력 | 비용 |
|---|---|---|---|---|---|
| 계획, 구현, 분석, 문서 작성 (코딩 에이전트와 하위 에이전트) | Claude Opus 5.5 | 11.5M | 1,314.0M | 4.5M | $424.48 |
| 데이터 생성, v1.0 | Haiku / Sonnet / Opus 5.5 (배치) | 396.1M | — | 1,189.5M | $740.91 |
| 데이터 생성, 시범과 이전 라운드 | Haiku / Sonnet / Opus 5.5 | 183.7M + 9.9M | 7.1M | 679.6M + 2.5M | $263.83 + $85.58 |
| 라벨 검수 (블라인드 재태깅, 감사, 판정) | Claude Opus 5.5 (감사 하나는 Sonnet 5.5) | 30.1M | 16.9M | 3.9M | $317.30 |
| 라벨 검수 | GPT-6.1 Sol | 4.5M | 9.0M | 1.0M | $19.63 |
| 설계·코드·문서 리뷰 | GPT-6.1 Sol | 3.1M | 18.0M | 0.9M | $16.96 |
| 학습과 평가 | Colab GPU, 96.4 컴퓨트 유닛 | — | — | — | $9.63 |
| 시험 호출 | | 0.1M | 0.1M | 0.0M | $0.32 |
| ZCode로 한 중국어 점검 | GLM-5.3 | 총 7.3M 토큰 (콘솔이 입력과 출력을 나누지 않음) | | | $10.22–32.12 |
| **합계** | | | | | **$1,888.85–1,910.75** |

ZCode 수치는 z.ai 콘솔의 앱 사용량이다. 모든 토큰을 입력으로 칠 때($1.40/100만)와 출력으로 칠 때($4.40/100만)의 범위로 적었다.
에이전트의 토큰은 대부분 입력이므로 실제 비용은 아래쪽에 가깝다. 검수 비용의
약 3분의 1은 15행마다 다시 보내는 코딩 CLI 자체 프롬프트(약 2.9만 토큰)다. 남은 검수는 두 번째 의견이 필요한 행만 보낸다.

---

## 5. 모델

**교사 모델(비공개).** mDeBERTa-v3-base(278M 파라미터, 5 에포크)와 XLM-R-large(559M, 2 에포크)를 v1.0 학습 데이터 1,069,002행으로
미세조정했다. 학생 모델의 소프트 타깃을 만드는 데만 쓰고 공개하지 않는다.

**학생 모델(공개).** 네 모델 모두 mMiniLMv2-L6-H384에서 출발하며, XLM-R 어휘를 학습 데이터에 나오는 35,298개 조각으로 가지치기했다.
등급별로 압축한 뒤 두 교사의 평균 소프트 레이블로 2 에포크 증류했다(α 0.5, T 2). 4비트 가중치 ONNX로 내보내며, `q4e`는 임베딩도
4비트로 저장한다.

| 등급 | 구조 | 파라미터 | 내려받기(기본 파일) |
|---|---|---|---|
| large | 6층, 가지치기한 임베딩 전체 | 24.4M | 15.5 MB (q4e) |
| medium (SDK 기본) | 6층, 임베딩 rank 192 | 17.7M | 11.3 MB (q4e) |
| small | 4층, rank 64 | 9.6M | 7.1 MB (q4) |
| nano | 공유 층 하나를 4번 적용, rank 32 | 3.1M | 2.6 MB (q4) |

공개 테스트에서 4비트 임베딩은 large와 medium의 놓침률을 최대 0.1점 바꾸고 2.7–5.2 MB를 줄인다. small과 nano에서는 1 MB도
줄지 않으면서 놓침률이 0.2–0.3점 늘어서 이 둘은 `q4`가 기본이다. 결정 임계값 하나("바깥" 로짓에 더하는 편향, `sensitivity`로 노출)로 두 번째 모델 없이
놓침과 과잉 마스킹을 맞바꿀 수 있다(부록 O.1–O.2).

**브라우저에서.** SDK `@newmean-ai/pii`는 같은 ONNX 파일을 읽는 두 엔진 중 하나로 모델을 WebAssembly에서 실행한다.
onnxruntime-web, 그리고 이 네 모델 전용으로 만든 내장 엔진이다(독립 실행 C를 SIMD WebAssembly로 컴파일했고, 브라우저가 relaxed
SIMD를 지원하면 융합 곱셈-덧셈을 쓰는 빌드를 쓴다). 두 엔진 모두 412개 텍스트 일치 검사 세트에서 Python 평가기와 같은 구간을
낸다(nano q4는 410개. 두 엔진에서 같은 두 텍스트이며, 공용 디코더가 겹치는 구간을 합치는 데서 생긴다). 모델과 토크나이저
말고 더 내려받는 실행기 크기와, headless Chrome에서의 지연 시간(1스레드, medium q4e, Apple M5 Pro):

| 실행기 | 내려받기(brotli) | 로딩 | 40토큰 | 251토큰 |
|---|---|---|---|---|
| 내장 엔진, SIMD128* | 19 KB (JS + wasm) | 63 ms | 23 ms | 147 ms |
| 내장 엔진, relaxed SIMD* | 19 KB | — | 14 ms | 91 ms |
| onnxruntime-web 1.31 (npm) | 2.45 MB | 161 ms | 30 ms | 140 ms |
| onnxruntime-web, 우리 연산만 넣은 최소 빌드 | 0.34–0.43 MB | 36–40 ms | 27–29 ms | 128–139 ms |
| LiteRT.js 2.6 (우리 모델을 .tflite 4비트로 변환) | 2.15 MB | 74 ms | 80 ms | 328 ms |
| onnxruntime-web WebGPU (q4e) | 원본 28.7 MB | 334 ms | 14 ms | 15 ms |

\* 내장 엔진 행은 엔진 벤치마크의 값이다(40 / 252토큰; 로딩은 페이지 이동부터 준비까지; 같은 벤치마크에서 onnxruntime-web은
31 / 146 ms, 230 ms). 나머지 행은 실행기 벤치마크의 값이다(40 / 251토큰; 모델 로딩만; 부록 P.2). Colab L4 VM(Xeon 2.20 GHz, Chrome, nano)에서도 순서는 같다: 내장 엔진 33–39 / 222–249 ms,
onnxruntime-web 63–65 / 287–294 ms.

**휴대폰에서.** SDK 기본 엔진은 내장 엔진이다. 휴대폰 네 대(iPhone 한 대, Android 세 대; 기기마다 중앙값을 낸 뒤 평균)에서 짧은
텍스트는 onnxruntime-web보다 약 1.9배, 긴 텍스트는 약 24% 빠르고, 처음 불러오는 데 0.77초가 걸린다(onnxruntime-web 2.85초).
모든 휴대폰, 크기, 엔진에서 54개 언어 108개 텍스트의 구간이 데스크톱 기준과 같았고, 반복 실행도 같은 구간을 냈다.

| 휴대폰 4대 평균 (ms, 40 / 252 / 1,000 토큰) | nano | small | medium | large |
|---|---|---|---|---|
| 내장 엔진 (SDK 기본) | 17 / 112 / 574 | 17 / 115 / 583 | 26 / 175 / 890 | 26 / 174 / 881 |
| onnxruntime-web 1.31 | 33 / 155 / 755 | 32 / 156 / 772 | 48 / 237 / 1,155 | 48 / 237 / 1,161 |

WebGPU는 실행기가 28.7 MB라 당분간 넣지 않는다. 세부 수치, Firefox, Safari(WebKit), 휴대폰, 네이티브 Python 속도는 부록 P.

## 6. 평가

주 지표는 **문자 놓침률**(character miss rate)이다. 공백이 아닌 PII 문자 중 마스킹되지 않고 남은 비율로, 프라이버시와 직결되는
수치다. 과잉 마스킹(over-masking; 비PII 문자 중 마스킹된 비율)도 보고하고, 정확 일치 F1(시작, 끝, 레이블이 모두 같음)은 부록 O에
있다. 공개 탐지기는 권장 설정과 우리 어댑터로 실행하고, 각자의 레이블에 맞춰 접은 정답으로 채점한다(수준 2 → 수준 1 레이블,
비밀값 계열 → SECRET). 놓침률과 과잉 마스킹은 레이블과 무관하다. 모든 수치는 모델만(정규식 계층 없음) 쓴 단일 실행이며, 우리
학생 모델의 시드 간 차이는 0.2–0.6점이다. 모두 Colab L4 VM에서 실행했다. 우리 학생 모델과 q4 ONNX 탐지기는 VM의 CPU(Xeon 2.20 GHz,
onnxruntime)에서, 트랜스포머 탐지기는 L4 GPU에서 돌렸다.

**우리 데이터셋.** 체크포인트와 설정은 검증셋으로만 고른다. 공개 테스트는 모델을 확정한 뒤 채점한다. 비공개 테스트는
리더보드용으로 남겨 둔다. 리더보드는 Hugging Face Space로, 공개 테스트는 레이블과 언어별로, 비공개 테스트는 모델당 종합 점수
하나만 보여 주며, 채점은 Hugging Face Job(L4)이 매주 한다. 첫 채점(2026-10-31, 아래 표의 13개 모델 전부)에서 모든 모델의 놓침률이
공개 테스트와 1점 안팎으로 같았다. 우리 모델은 large부터 nano까지 1.5 / 2.0 / 2.4 / 3.0%(공개 1.6 / 2.1 / 2.7 / 3.3%), 공개 탐지기는
−0.4~+1.0점 차이였다. 즉 공개 테스트가 아무도 보지 않은 셋보다 우리 모델에 유리하지 않았다.

**외부 데이터셋.** 상업적으로 쓸 수 있는 데이터셋 네 개이며, 어느 것도 v1.0 모델 학습에 쓰지 않았다. 공식 test split이 있으면 거기서
표본을 뽑고, 없으면 우리 모델이 학습하지 않은 split을 쓴다:

| 데이터셋 | 공식 split | 우리 평가 표본 | 행 수 | 언어 | 라이선스 |
|---|---|---|---|---|---|
| openpii-1.5m [21] | train, validation (test 없음) | validation에서 언어당 무작위 200행 | 6,000 | 30 | CC BY 4.0 |
| KDPII [25, 26] | 없음 | 문서를 섞어(시드 20261028) 문서 단위로 85/15 분할, 15% 쪽의 모든 문장 | 8,129 | ko | CC BY 4.0 |
| Nemotron-PII [22] | train, test | test에서 로케일(us, intl)당 무작위 1,000행 | 2,000 | en | CC BY 4.0 |
| Gretel finance [23] | train, test | test에서 언어당 최대 400행 | 2,794 | 7 | Apache-2.0 |

레이블은 우리 체계로 옮긴다. 우리 지침 밖의 유형(회사명, 일반 날짜, 단독 도시명, 나이 등)은 Nemotron과 Gretel에서 `ignore`로
표시하므로, 그 위의 예측은 탐지로도 과잉 마스킹으로도 세지 않는다(M15). 원본 데이터는 재배포하지 않는다. 저장소에는 각 표본의
행 ID, 데이터셋 버전(revision), SHA-256이 있고, `ext_data.py eval`이 원본 내려받기 파일로부터 네 파일을 바이트 단위까지 똑같이
다시 만든다.

**결과**(놓침 % / 과잉 마스킹 %; 우리 학생 모델은 배포하는 기본 파일 그대로). 공개 테스트 열은 실제 값과 겹칠 수 있는 값을
569행에서 바꾼 공개 테스트 1.1판(2026-10-31, 8절)의 점수다. 1.0판과 비교하면 우리 학생 모델은 최대 0.05점, 다른 탐지기는
0.2–1.6점 달라졌다. 부록 O의 세부 표는 1.0판 기준이다.

| 모델 | 파라미터 | 내려받기 | 공개 테스트 | KDPII (ko) | openpii (30개 언어) | Nemotron (en) | Gretel finance | ms/행, CPU 1스레드 | ms/행, 12스레드 또는 GPU |
|---|---|---|---|---|---|---|---|---|---|
| **우리 large** | 24.4M | 15.5 MB | **1.6** / 0.6 | **7.6** / 2.4 | 4.6 / 4.2 | **7.4** / 0.9 | 20.3 / 5.2 | 88 | 23 |
| **우리 medium** | 17.7M | 11.3 MB | 2.1 / 0.6 | 9.8 / 2.0 | 6.1 / 4.1 | 7.9 / 0.7 | 22.5 / 4.2 | 88 | 23 |
| **우리 small** | 9.6M | 7.1 MB | 2.7 / 1.2 | 9.5 / 3.2 | 5.6 / 5.5 | 10.6 / 1.0 | 24.1 / 5.7 | 59 | 16 |
| **우리 nano** | 3.1M | 2.6 MB | 3.3 / 2.1 | 9.5 / 4.2 | 4.9 / 7.0 | 10.6 / 2.1 | 22.8 / 7.5 | 59 | 16 |
| gliner-pii-edge | 45M | 176 MB | 19.6 / 31.9 | 11.9 / 45.9 | 5.7 / 15.0 | 17.3 / 0.6 | **15.7** / 8.7 | — | 9 (GPU) |
| OpenAI privacy-filter | 1.4B | 2.8 GB | 34.6 / 4.4 | 34.8 / 2.1 | 14.3 / 6.6 | 25.7 / 0.2 | 32.2 / 2.7 | — | 85 (GPU) |
| Horizon pii-redactor-small | 141M | 563 MB | 41.0 / 1.1 | 25.7 / 1.1 | 11.1 / 0.0 | 7.8 / 0.1 | 31.6 / 1.8 | — | 10 (GPU) |
| KoELECTRA 한국어 PII NER | 14M | — | 43.1 / 10.2 | 8.8 / 1.1 | 24.1* / 11.3 | 45.9 / 0.5 | 37.6 / 11.0 | — | 6 (GPU) |
| GLiNER2-PII | 307M | 1.2 GB | 44.9 / 1.6 | 34.2 / 4.2 | 23.2 / 3.5 | 46.3 / 0.4 | 32.9† / 1.7 | — | 20 (GPU) |
| Rampart | ≈19M | 15 MB | 56.5 / 3.0 | 36.1 / 3.5 | **3.5*** / 2.2 | 51.6 / 0.1 | 40.5 / 1.0 | 130 | — |
| Mozilla smart-pii-redactor v1 | ≈19M | 15 MB | 56.9 / 2.0 | 46.0 / 2.1 | 25.9 / 3.8 | 48.8 / 0.1 | 32.9 / 0.4 | 90 | — |
| Rampart-edge | ≈2M | 1.5 MB | 74.7 / 1.5 | 89.0 / 0.0 | 46.1 / 2.5 | 55.5 / 0.1 | 48.0 / 0.9 | 54 | — |
| 정규식 기준선 | — | — | 75.3 / 0.6 | 62.0 / 0.1 | 61.1 / 0.1 | 44.6 / 0.1 | 86.7 / 3.0 | 1 | — |

\* openpii 데이터로 학습했거나(Rampart), 우리 openpii 6,000행 중 193행이 든 데이터셋으로 학습했다(KoELECTRA). 속도는 공개
테스트셋 기준이며, CPU 1스레드는 작업 6개가 VM을 나눠 쓴 상태, 12스레드는 단독 실행이다. GPU 행은 CPU 행과 비교할 수 없다.
† GLiNER2-PII는 Gretel의 긴 문서를 통째로 넣으면 한 번에 하나씩 넣어도 L4 메모리가 부족했다. 이 값은 라이브러리의 긴 글 모드
(384단어씩 겹치게 나눔)로 쟀고, 이 모드는 공개 테스트 앞 300행에서 기존 예측과 똑같다.
공개 탐지기의 파라미터 수는 가중치 파일에서 셌다. 2절에 인용한 모델 카드는 205M(GLiNER2-PII)과 1.5B(OpenAI privacy-filter)로
적는다.

- **공개 테스트**(Sonnet 5.5와 Opus 5.5가 생성했고 — 우리 학습 데이터는 Haiku와 Sonnet이 생성 — 4.2절처럼 검수했다). 우리 학생
  모델은 같은 설계와 지침의 데이터로 학습했고, 공개 탐지기는 이 데이터를 처음 보며 일부는 조직 같은 레이블을 일부러 가리지
  않는다. 이 열은 각 탐지기가 이 마스킹 정책에 얼마나 맞는지를 보여 줄 뿐 일반적인 품질을 보여 주지는 않는다.
- **세부(medium, 공개 테스트).** 라틴 문자 언어 1.9%, 그 밖의 문자 2.4%이고, 가장 높은 언어는 카자흐어 4.4%, 태국어 3.7%, 영어 3.6%다. 수준 1은
  2.0%, 수준 2는 4.6%. 레이블별로는 조직(7.0%), 주소(4.4%), 사람 이름(3.3%)을 가장 많이 놓치고, 이메일, 전화, IP, 비밀값은 1% 미만이다.
  우리 학습 데이터에 없는 생성기인 Opus가 쓴 행은 Sonnet 행보다 더 많이 놓친다(2.9% 대 1.5%). 같은 분포에서 오는 이점은 실제로 있고,
  처음 보는 텍스트에 대한 수치는 높은 쪽이다.
- **외부 데이터셋**(우리 모델이 한 번도 학습하지 않음). KDPII와 Nemotron에서는 large 학생 모델이 가장 적게 놓치지만 차이가
  늘 크지는 않다. KDPII에서는 한국어 전용 KoELECTRA(8.8%)가 우리 medium, small, nano(9.5–9.8%)보다 덜 놓치고, Nemotron에서는
  Horizon(7.8%)이 medium과 비슷하고 small, nano(10.6%)보다 앞선다. openpii에서는 openpii 데이터로 학습한 Rampart가 가장 적게
  놓친다. Gretel finance에서는 gliner-pii-edge가 더 높은 과잉 마스킹을 대가로 가장 적게 놓치고, 나머지 모델은 모두 5분의 1 이상을
  놓친다.
- **정규식 계층**(SDK에서 선택 사항, 기본은 꺼짐). 패턴을 더하면 공개 테스트에서 놓침률은 최대 0.03점 줄고 과잉 마스킹은 두
  배가 된다(medium 0.6% → 1.2%). 주로 계좌번호 패턴이 날짜와 타임스탬프에도 맞기 때문이다(부록 O.5).
- **크기와 속도.** large와 medium은 층 수가 같아 속도도 같고, 4비트 임베딩(q4e)은 속도를 떨어뜨리지 않는다. 2.6–15.5 MB 학생
  모델은 서버 CPU 12스레드에서 텍스트 하나에 16–23 ms가 걸린다. 특정 셋에서 가장 가까이 따라온 공개 탐지기(KDPII의 KoELECTRA,
  Nemotron의 Horizon, Gretel의 gliner-pii-edge)는 파라미터가 14M–141M이고 GPU에서 돌렸다. 브라우저와 휴대폰 지연 시간은 5절.

공개 테스트의 레이블별, 수준별, 언어별 표와 q4 대 q4e, 임계값 조정은 부록 O에 있다. 이전 비교(부록 L)는 공개 탐지기의 비밀값
계열 레이블을 버리던 이전 레이블 체계를 썼으며, 위 수치가 그것을 대신한다.

## 7. 한계

- **단일 실행, 몇 안 되는 기기.** 모델 수치는 단일 실행이다(시드 간 차이 0.2–0.6점). 휴대폰 지연 시간은 네 대(iPhone 한 대,
  삼성 Android 세 대)의 평균이다.
- **합성–실제 격차.** 학습 데이터는 모두 생성한 것이다. 외부 데이터셋은 무엇이 옮겨 가는지 보여 주지만, 실제 사용자 텍스트는
  더 지저분하다.
- **데이터셋마다 레이블 관례가 다르다**(URL, 비밀값, 조직, 부분 날짜). 그래서 외부 수치에는 모델 오류와 관례 차이가 섞여 있다.
  관례가 갈리는 곳에서는 레이블별 수치를 보고한다.
- **실행 간 편차**가 외부 데이터셋에서 1.5–2점이다(부록 I.3). 그만한 크기의 단일 실행 간 격차는 확립된 것이 아니다.
- **이름 풀**은 Wikidata에서 왔고 공인 쪽으로 치우친다. 크메르어 여성 이름과 일부 출신국 풀은 빈약하다.
- **테스트셋의 SECRET 스팬은 생성기 하나(Sonnet)가 썼다.** Opus가 비밀값 작성을 거부하기 때문이다.
- **검수는 값이 아니라 레이블을 다룬다.** 감사는 스팬과 레이블을 확인할 뿐, 값이 현실적인지 또는 실제 사람의 것인지는 확인하지
  않는다. 변경률은 100–300행 표본에서 나왔고, 두 검수자가 함께 저지른 오류는 놓친다.
- **PII 수준은 정책이다.** 성이나 별명이 누군가를 식별하는지는 문맥에 달려 있다. 수준 2는 사용자가 보이게 남기기로 선택할 수
  있는 스팬을 표시할 뿐, 안전한 스팬을 뜻하지 않는다.
- **마스킹은 결코 100%가 아니다.** 약속 대신 언어별·레이블별 놓침률을 공개한다.

## 8. 윤리와 공개

**가짜 값.** 생성된 값이 실제 값과 우연히 같을 수 있다. 프롬프트는 예약된 이메일 도메인(RFC 2606), 가상의 전화번호 대역을
예약해 둔 국가의 그 대역, 체크섬이 틀린 한국 주민등록번호로 된 시드 값을 제시하고, 지침은 형식은 유효하되 가상인 식별번호를
요구한다. 그러나 생성 단계에서는 이를 검사하지 않는다. 공개 뒤 공개 테스트, 검증, 학습 데이터의 모든 값을 검사해 실제 값과
겹칠 수 있는 값을 바꿨다. 검증 숫자가 맞는 신분증·카드·IBAN 번호(숫자 한 자리 변경), 무료 메일 주소(예약 도메인으로 이동),
유명인 이름이다(공개 테스트 1.1판 569행, 검증 1,923행, 학습 39,724행). 그래도 모든 값이 가상이라고 주장하지 않으며, 요청이
있으면 어떤 값이든 제거한다(hoonjae@newmean.ai).

**마스킹.** SDK는 텍스트를 보내기 전에 탐지한 스팬을 마스킹한다. 놓친 것은 마스킹되지 않은 채 전송된다(7절).

**공개.** 모델과 SDK는 Apache-2.0이고, "Powered by newmean.ai" 표시를 (조건이 아니라) 요청한다. SDK는 npm의
`@newmean-ai/pii`(코드는 https://github.com/newmean-ai/pii), 네 모델은 https://huggingface.co/newmean-ai (newmean-pii-nano,
newmean-pii-small, newmean-pii-medium, newmean-pii-large)에 있다. newmean-pii-dataset의 공개 테스트(https://huggingface.co/datasets/newmean-ai/pii-dataset)는
CC BY-NC 4.0으로 연구와 평가에 무료, 상업 사용은 문의. 공개·비공개 테스트 점수는
https://huggingface.co/spaces/newmean-ai/pii-leaderboard 에, 데모는 https://pii.newmean.ai 에 있다. 학습·검증 데이터는 [양식](https://docs.google.com/forms/d/e/1FAIpQLScSMp2UvhNIlzc0EkDc2i6TwqKSzFc4bLR45DCnZSESlJzHzA/viewform)을 통한 요청으로 제공한다(신청은 지금 받고 데이터는 2026년 12월부터 공유; 연구는 비상업 라이선스로 무료; 사업 사용은 상업 라이선스로, 문의; 연락처
hoonjae@newmean.ai). 비공개 테스트와 교사 모델은 비공개로 남는다. 생성기 코드, 프롬프트, 국가 명세, 이름 풀은 공개하지 않으며,
이 보고서와 부록 M이 방법을 설명한다.

---

## 저자별 기여

- **Claude Opus 5.5** (Anthropic) — 데이터 파이프라인, 모델, 평가 구현; 실험 수행; 이 보고서와 모든 기록의 초안 작성. 사람
  저자가 감독하고 검토했다.
- **GPT-6.1 Sol** (OpenAI, codex CLI 사용) — 독립 검토: 데이터 표본과 테스트셋의 블라인드 재주석, 파이프라인 설계 검토, 이
  보고서 검토.
- **Hoonjae Lee** (newmean.ai) — 연구 방향, 범위·데이터 정책·공개에 관한 결정, 모든 결과와 이 보고서의 검토; 보고서 내용에
  책임을 진다.

## AI 및 생성 데이터 사용

학습·테스트 데이터는 Claude Haiku 5.5, Sonnet 5.5, Opus 5.5(Anthropic)로 생성했다. 검수에는 Claude Opus 5.5, GPT-6.1 Sol,
그리고 중국어 검수와 공개 테스트 25행 판정에는 ZCode(Z.ai)를 통한 GLM을 썼다. 이 보고서의 본문은 Claude Opus 5.5가 초안을 쓰고 GPT-6.1 Sol과 사람 저자가
검토했으며, 사람 저자가 내용에 책임을 진다. Claude Opus 5.5와 GPT-6.1 Sol을 저자로 올린 것은 이 작업에서의 역할을 나타낸다.
Anthropic과 OpenAI는 이 보고서를 검토하거나 보증하지 않았다. 모든 수치는 공개하지 않는 내부 실험 기록에서
가져왔으며, 두 부록에 근거 자료를 실었다.

## 이해 상충

newmean.ai는 여기서 설명한 학습 데이터의 상업 라이선스를 판매하며, 그 때문에 생성기를 비공개로 둔다. 모델과 SDK는 무료(Apache-2.0)이고,
공개 테스트는 연구와 평가에 무료(CC BY-NC 4.0)다.

## 참고문헌

[1] Microsoft. *Presidio* (GitHub repository). https://github.com/microsoft/presidio

[2] Presidio documentation. *Multi-language support*. https://presidio.dataprivacystack.org/analyzer/languages/

[3] U. Zaratiana, N. Tomeh, P. Holat, T. Charnois. *GLiNER: Generalist Model for Named Entity Recognition using Bidirectional Transformer*. arXiv:2311.08526, 2023. https://arxiv.org/abs/2311.08526

[4] U. Zaratiana. *urchade/gliner_multi_pii-v1* (model card). Hugging Face. https://huggingface.co/urchade/gliner_multi_pii-v1

[5] U. Zaratiana et al. *GLiNER2: An Efficient Multi-Task Information Extraction System with Schema-Driven Interface*. arXiv:2507.18546, 2025. https://arxiv.org/abs/2507.18546

[6] Fastino. *GLiNER2-PII: Multilingual PII Detection & Masking* (`fastino/gliner2-privacy-filter-PII-multi`, model card). Hugging Face. https://huggingface.co/fastino/gliner2-privacy-filter-PII-multi

[7] Knowledgator. *knowledgator/gliner-pii-edge-v1.0* (model card). Hugging Face. https://huggingface.co/knowledgator/gliner-pii-edge-v1.0

[8] OpenAI. *openai/privacy-filter* (model card). Hugging Face. https://huggingface.co/openai/privacy-filter

[9] R. Uppala. *OpenAI Privacy Filter: A Cross-Lingual, Cross-Domain PII Evaluation Across 32 Benchmarks*. arXiv:2608.02616, 2026. https://arxiv.org/abs/2608.02616

[10] Mozilla. *Mozilla/smart-pii-redactor-v1-onnx* (model card). Hugging Face. https://huggingface.co/Mozilla/smart-pii-redactor-v1-onnx

[11] National Design Studio. *Rampart* (GitHub repository). https://github.com/nationaldesignstudio/rampart

[12] National Design Studio. *Rampart Edge* (GitHub repository). https://github.com/nationaldesignstudio/rampart-edge

[13] Horizon Labs. *Horizon-Labs/pii-redactor-small* (model card). Hugging Face. https://huggingface.co/Horizon-Labs/pii-redactor-small

[14] Google Cloud. *InfoType detector reference*. Sensitive Data Protection documentation. https://docs.cloud.google.com/sensitive-data-protection/docs/infotypes-reference

[15] Amazon Web Services. *Detecting PII entities*. Amazon Comprehend Developer Guide. https://docs.aws.amazon.com/comprehend/latest/dg/how-pii.html

[16] G. Vats et al. *REDACT: A Systematically Controlled Multilingual Benchmark for Personal Information Detection*. arXiv:2606.19881, 2026. https://arxiv.org/abs/2606.19881

[17] S. Brynjólfsson et al. *RedactionBench*. arXiv:2606.18782, 2026. https://arxiv.org/abs/2606.18782

[18] K. Zhang et al. *PII-TRACE: A Benchmark for Context-Aware PII Detection in Multi-Turn LLM Conversations*. arXiv:2609.22200, 2026. https://arxiv.org/abs/2609.22200

[19] Ai4Privacy. *ai4privacy/pii-masking-400k* (dataset card). Hugging Face. https://huggingface.co/datasets/ai4privacy/pii-masking-400k

[20] Ai4Privacy. *Open PII Masking 500k Ai4Privacy Dataset* (`ai4privacy/open-pii-masking-500k-ai4privacy`, dataset card). Hugging Face. https://huggingface.co/datasets/ai4privacy/open-pii-masking-500k-ai4privacy

[21] Ai4Privacy. *OpenPII 1.5M: Multilingual PII Masking Dataset with Asia Pacific Extension* (`ai4privacy/pii-masking-openpii-1.5m`). Hugging Face, 2026. https://huggingface.co/datasets/ai4privacy/pii-masking-openpii-1.5m

[22] NVIDIA. *Nemotron-PII: Synthesized Data for Privacy-Preserving AI* (`nvidia/Nemotron-PII`, dataset card). Hugging Face, 2025. https://huggingface.co/datasets/nvidia/Nemotron-PII

[23] Gretel.ai. *gretelai/synthetic_pii_finance_multilingual* (dataset card). Hugging Face. https://huggingface.co/datasets/gretelai/synthetic_pii_finance_multilingual

[24] R. Roller et al. *DialogPII: A multilingual dataset of synthetic dialog transcripts to detect personal information*. arXiv:2606.30312, 2026. https://arxiv.org/abs/2606.30312

[25] L. Fei, Y. Kang, S. Park, Y. Jang, J. Lee, H. Kim. *KDPII: A New Korean Dialogic Dataset for the Deidentification of Personally Identifiable Information*. IEEE Access 12:135626–135641, 2024. https://doi.org/10.1109/ACCESS.2024.3461804

[26] Yonsei University HamSaeM Kim's Lab and TSCIENTIFIC. *KDPII DATASET REVISED* (v2). Zenodo, 2024. https://zenodo.org/records/16759166

[27] X. Pan, B. Zhang, J. May, J. Nothman, K. Knight, H. Ji. *Cross-lingual Name Tagging and Linking for 282 Languages*. ACL 2017, pp. 1946–1958. https://aclanthology.org/P17-1178/

[28] S. Tedeschi, V. Maiorca, N. Campolungo, F. Cecconi, R. Navigli. *WikiNEuRal: Combined Neural and Knowledge-based Silver Data Creation for Multilingual NER*. Findings of EMNLP 2021, pp. 2521–2533. https://aclanthology.org/2021.findings-emnlp.215/

[29] A. Stubbs, Ö. Uzuner. *Annotating longitudinal clinical narratives for de-identification: The 2014 i2b2/UTHealth corpus*. Journal of Biomedical Informatics 58(Suppl):S20–S29, 2015. https://pubmed.ncbi.nlm.nih.gov/26319540/

[30] I. Pilán, P. Lison, L. Øvrelid, A. Papadopoulou, D. Sánchez, M. Batet. *The Text Anonymization Benchmark (TAB): A Dedicated Corpus and Evaluation Framework for Text Anonymization*. Computational Linguistics 48(4):1053–1101, 2022. https://aclanthology.org/2022.cl-4.19/

[31] S. Wang, Y. Liu, Y. Xu, C. Zhu, M. Zeng. *Want To Reduce Labeling Cost? GPT-3 Can Help*. Findings of EMNLP 2021, pp. 4195–4205. https://aclanthology.org/2021.findings-emnlp.354/

[32] J. Ye et al. *ZeroGen: Efficient Zero-shot Learning via Dataset Generation*. EMNLP 2022. arXiv:2202.07922. https://arxiv.org/abs/2202.07922

[33] W. Zhou, S. Zhang, Y. Gu, M. Chen, H. Poon. *UniversalNER: Targeted Distillation from Large Language Models for Open Named Entity Recognition*. ICLR 2024. arXiv:2308.03279. https://arxiv.org/abs/2308.03279

[34] Y. Yu et al. *Large Language Model as Attributed Training Data Generator: A Tale of Diversity and Bias*. NeurIPS 2023 Datasets and Benchmarks. arXiv:2306.15895. https://arxiv.org/abs/2306.15895

[35] Z. Li, H. Zhu, Z. Lu, M. Yin. *Synthetic Data Generation with Large Language Models for Text Classification: Potential and Limitations*. EMNLP 2023, pp. 10443–10461. https://aclanthology.org/2023.emnlp-main.647/

[36] Z. Tan et al. *Large Language Models for Data Annotation and Synthesis: A Survey*. EMNLP 2024. arXiv:2402.13446. https://arxiv.org/abs/2402.13446

[37] A. Conneau et al. *Unsupervised Cross-lingual Representation Learning at Scale*. ACL 2020, pp. 8440–8451. https://aclanthology.org/2020.acl-main.747/

[38] P. He, J. Gao, W. Chen. *DeBERTaV3: Improving DeBERTa using ELECTRA-Style Pre-Training with Gradient-Disentangled Embedding Sharing*. ICLR 2023. arXiv:2111.09543. https://arxiv.org/abs/2111.09543

[39] W. Wang, H. Bao, S. Huang, L. Dong, F. Wei. *MiniLMv2: Multi-Head Self-Attention Relation Distillation for Compressing Pretrained Transformers*. Findings of ACL-IJCNLP 2021, pp. 2140–2151. https://aclanthology.org/2021.findings-acl.188/

[40] Microsoft. *MiniLM* (unilm GitHub repository, multilingual checkpoints). https://github.com/microsoft/unilm/tree/master/minilm

[41] M. Marone, O. Weller, W. Fleshman, E. Yang, D. Lawrie, B. Van Durme. *mmBERT: A Modern Multilingual Encoder with Annealed Language Learning*. arXiv:2509.06888, 2025. https://arxiv.org/abs/2509.06888

[42] G. Hinton, O. Vinyals, J. Dean. *Distilling the Knowledge in a Neural Network*. arXiv:1503.02531, 2015. https://arxiv.org/abs/1503.02531

[43] S. Mukherjee, A. H. Awadallah. *XtremeDistil: Multi-stage Distillation for Massive Multilingual Models*. ACL 2020, pp. 2221–2234. https://aclanthology.org/2020.acl-main.202/

[44] X. Wang, Y. Jiang, N. Bach, T. Wang, F. Huang, K. Tu. *Structure-Level Knowledge Distillation For Multilingual Sequence Labeling*. ACL 2020, pp. 3317–3330. https://aclanthology.org/2020.acl-main.304/

[45] A. Abdaoui, C. Pradel, G. Sigel. *Load What You Need: Smaller Versions of Mutililingual BERT*. SustaiNLP 2020, pp. 119–123. https://aclanthology.org/2020.sustainlp-1.16/

[46] A. Ushio, Y. Zhou, J. Camacho-Collados. *An Efficient Multilingual Language Model Compression through Vocabulary Trimming*. Findings of EMNLP 2023. arXiv:2305.15020. https://arxiv.org/abs/2305.15020

[47] E. Frantar, S. Ashkboos, T. Hoefler, D. Alistarh. *GPTQ: Accurate Post-Training Quantization for Generative Pre-trained Transformers*. ICLR 2023. arXiv:2210.17323. https://arxiv.org/abs/2210.17323

[48] ONNX Runtime. *Quantize ONNX models* (section "Quantize to Int4/UInt4"). https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html

[49] ONNX Runtime. *How to add machine learning to your web application with ONNX Runtime* (ONNX Runtime Web). https://onnxruntime.ai/docs/tutorials/web/

[50] P. Lewis, P. Stenetorp, S. Riedel. *Question and Answer Test-Train Overlap in Open-Domain Question Answering Datasets*. EACL 2021, pp. 1000–1008. https://aclanthology.org/2021.eacl-main.86/

[51] K. Lee et al. *Deduplicating Training Data Makes Language Models Better*. ACL 2022, pp. 8424–8445. https://aclanthology.org/2022.acl-long.577/

[52] I. Augenstein, L. Derczynski, K. Bontcheva. *Generalisation in Named Entity Recognition: A Quantitative Analysis*. Computer Speech and Language, 2017. arXiv:1701.02877. https://arxiv.org/abs/1701.02877

[53] O. Sainz, J. Campos, I. García-Ferrero, J. Etxaniz, O. Lopez de Lacalle, E. Agirre. *NLP Evaluation in trouble: On the Need to Measure LLM Data Contamination for each Benchmark*. Findings of EMNLP 2023, pp. 10776–10787. https://aclanthology.org/2023.findings-emnlp.722/

[54] N. Reimers, I. Gurevych. *Reporting Score Distributions Makes a Difference: Performance Study of LSTM-networks for Sequence Tagging*. EMNLP 2017, pp. 338–348. https://aclanthology.org/D17-1035/

[55] J. Dodge, G. Ilharco, R. Schwartz, A. Farhadi, H. Hajishirzi, N. Smith. *Fine-Tuning Pretrained Language Models: Weight Initializations, Data Orders, and Early Stopping*. arXiv:2002.06305, 2020. https://arxiv.org/abs/2002.06305

[56] S. Bogdanov, A. Constantin, T. Bernard, B. Crabbé, E. P. Bernard. *NuNER: Entity Recognition Encoder Pre-training via LLM-Annotated Data*. EMNLP 2024, pp. 11829–11841. https://aclanthology.org/2024.emnlp-main.660/

[57] B. Kleinberg, T. Davies, M. Mozes. *Textwash — automated open-source text anonymisation*. arXiv:2208.13081, 2022. https://arxiv.org/abs/2208.13081

[58] D. Vrandečić, M. Krötzsch. *Wikidata: a free collaborative knowledgebase*. Communications of the ACM 57(10):78–85, 2014. https://www.wikidata.org/

[59] United Nations, Department of Economic and Social Affairs, Population Division. *International Migrant Stock 2024*. https://www.un.org/development/desa/pd/content/international-migrant-stock

[60] *Faker* (Python package). https://github.com/joke2k/faker

## 부록

- **[APPENDIX-methods.ko.md](APPENDIX-methods.ko.md)** — 데이터 방법 전체: M1 레이블 체계와 지침 · M2 설계 축 ·
  M3 PII 없는 항목 · M4 국가 형식 · M5 이름과 외국인 거주자 · M6 생성기, SECRET 재배정, 출력 형식 · M7 배치 생성과 고정된 입력 ·
  M8 생성 뒤 값 바꾸기 · M9 자동 점검 관문 · M10 블라인드 이중 검수 · M11 중복 제거와 누출 · M12 다양성 · M13 데이터셋 버전과
  레이블 대응 · M14 관련 연구 상세.
- **[APPENDIX-experiments.ko.md](APPENDIX-experiments.ko.md)** — 실험 기록: A 생성기 시범 · B 라운드별 블라인드 이중 검수 ·
  C 첫 전체 라운드에서 v1.0까지 · D 국가 형식 수정 · E 이름: 시범 결과 · F v1.0 이전 작업의 비용 · G 시범 단계 사고 · H 이전
  모델과 그 데이터 · I 교사 모델 · J 학생 모델: 어휘와 증류 · K 내보내기, 양자화, 등급 · L 공개 탐지기와의 비교 · N 학습용으로 검토한 외부 데이터셋 ·
  O 공개 테스트에서의 v1.0 모델 · P 브라우저 실행기와 네이티브 속도.
- 계획: 지침 버전과 그 근거가 된 검수 발견의 요약. 지침 원문, 프롬프트, 국가 명세는 공개하지 않는다(8절).
