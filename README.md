# dart-figure-check

English summary first. The full write-up in Korean follows below.

## English summary

An experiment on what to check before accepting a number that an LLM copies out of a financial table in a Korean annual report (DART filings).

**Setup**

- Ground truth is the official structured financial data from DART (OpenDART `fnlttSinglAcntAll`, consolidated, fiscal year 2025), not labels written by a person or a model.
- Six accounts per company (revenue, operating income, net income, total assets, total liabilities, total equity), current and prior period, so 12 cells per company.
- Sample: KOSPI annual reports filed in March 2026, taken in filing order. The first 10 companies that had all six accounts in the official data were used to build the checker. The next 20 were run only after the checker code and the row-name list were fixed.
- Model: IBM Granite 3.3 8B run locally (Ollama, M2 Pro 16GB), one call per company on the consolidated balance sheet and income statement.

**Two checks**

- P1, quote check: the model's quote appears verbatim in a source line and contains the number.
- P2, cell check: that line is in a consolidated statement table, the row name equals the account name, and the number in that period's column is the same. The amount is the cell number times the table's unit. The unit written by the model is not used.

**Results** (`results/llm_items_*.json`)

| | 10 development companies | 20 held-out companies | All 30 |
|---|---|---|---|
| Values the model produced (sometimes several per cell) | 138 | 336 | 474 |
| Correct against official data | 67 | 164 | 231 |
| Accepted by P1 / wrong among them | 100 / 52 | 154 / 86 | 254 / 138 |
| Accepted by P2 / wrong among them | 44 / 0 | 66 / 0 | 110 / 0 |
| Correct values P1 accepted and P2 rejected | 4 | 2 | 6 |
| Cells filled correctly through P2 / all cells | 44 / 120 | 65 / 240 | 109 / 360 |

Why P2 rejected the wrong values that P1 had accepted:

| Reason | Development (52) | Held-out (86) |
|---|---|---|
| The number in that period's column is different (for example a figure from two years ago given as last year's) | 22 | 43 |
| The row name is not the account (for example cost of sales given as revenue) | 21 | 38 |
| The number is not in the row the quote points to | 7 | 5 |
| No such table or row | 2 | 0 |

Reading the source tables (`results/parse*.json`): given the official current-period amount, the parser located the row for 60 of 60 accounts in the development set and 119 of 120 in the held-out set. In every located row the prior-period cell also matched the official amount. The one miss is a row named "revenue and equity-method income", which is not in the exact-match name list.

**Limits**

- Zero wrong out of 110 accepted values is a small sample, not an error rate.
- P2 filled 109 of 360 cells. The rest would still need a person.
- The six accounts were chosen because code can check the answer. Notes, segment tables and quarterly reports were not tested, and P2's assumptions may break there.
- One small local model, one call per company. A larger model may make different kinds of errors.
- The error-injection result in section 3 below is a specification test: the function that builds the wrong answers and P2's check share the same logic, so P2's zero there is by design.

DART calls use Treasurer's [opendart-py](https://github.com/TreasurerSquad/opendart-py) 0.1.0. To run it, see the commands under "실행" at the end.

---

DART 사업보고서 재무 표에서 LLM이 옮겨 적은 숫자를 받기 전에 무엇을 확인해야 하는지 재 본 실험입니다.

**결과 (2026-11-16, 유가증권시장 사업보고서 30곳, 정답은 DART 공식 재무 데이터)**
- 로컬 8B 모델(Granite 3.3)이 실제로 낸 값 474개 중 231개가 맞았습니다. 인용이 원문에 있고 그 안에 같은 숫자가 있으면 받는 검사(P1)는 틀린 값 138개를 받았고, 그중 65개는 같은 행의 다른 기간 숫자, 59개는 다른 행(예: 매출원가를 매출액으로)이었습니다.
- 표의 칸 위치, 행 이름, 기간 머리글까지 확인하는 검사(P2)는 틀린 값을 하나도 받지 않았습니다. 대신 P1이 받은 맞는 값 116개 중 6개를 더 거부했습니다.
- 코드가 원문 표에서 정답 행을 찾은 비율은 개발용 10곳 60/60, 처음 보는 20곳 119/120이고 찾은 행의 전기 값은 모두 공식 금액과 같았습니다.
- 검색(2026-11-19 추가): 표의 행을 통째로 임베딩하면 정답 행이 상위 5개에 든 계정이 처음 보는 곳 119개 중 22~25개로 BM25(57개)보다 낮았습니다. 숫자 칸을 빼고 행 이름을 앞에 두니 80~85개가 됐습니다(같은 덩어리의 BM25는 60개). 요약재무정보와 연결재무제표 섹션만 남기면 BM25 104개, 임베딩 101개로 비슷했습니다.

```
LLM 답(계정, 기간, 값, 단위, 인용, 위치)
 → P1: 인용이 원문 줄에 그대로 있나, 인용 안에 같은 숫자가 있나
 → P2: 그 줄이 연결재무제표 표에 있나, 행 이름이 계정과 같나, 그 기간 열의 숫자가 같나
 → 금액 = 칸의 숫자 × 표 단위(LLM이 적은 단위는 쓰지 않음)
```

Claude Code로 하루 동안 만든 실험입니다. DART 호출은 트레져러의 [opendart-py](https://github.com/TreasurerSquad/opendart-py) 0.1.0을 씁니다.

## 왜

앞선 프로젝트(미국 10-K로 데이터센터 투자안을 심사하는 에이전트, 비공개)는 P1로 값을 받았습니다. 그때 실행에서 받은 인용과 값에 오류를 넣어 보니
P1은 같은 인용 안의 다른 숫자를 고른 오류를 거르지 못했습니다. 재무 표는 한 행에 당기, 전기, 전전기 숫자가 나란히 있어서 이 오류가 생기기 쉬운 자리입니다.

## 정답과 표본

- 정답: 사람이나 AI가 적은 정답표 대신 DART 공식 재무 데이터(`fnlttSinglAcntAll`, 연결, 2025 사업보고서)의 당기와 전기 금액(원).
- 계정 6개: 매출액, 영업이익, 당기순이익, 자산총계, 부채총계, 자본총계.
- 표본: 2026년 3월에 접수된 유가증권시장 사업보고서(2025 회계연도, 정정 제외)를 접수번호 순으로 보며 계정 6개가 공식 데이터에 모두 있는 회사를
  앞에서부터 10곳(개발용)과 그다음 20곳(처음 보는 회사). 처음 보는 20곳은 검사 코드와 행 이름 목록을 정한 뒤에만 돌렸습니다.
  - 개발용: 진양산업, 대한유화, 국도화학, 현대모비스, 케이씨씨, 삼성전자, 하이스틸, 유니드, 삼성SDI, 제일기획
  - 처음 보는 20곳: 한화투자증권, 롯데에너지머티리얼즈, 삼성에스디에스, 이노션, 삼성전기, GS피앤엘, 고려산업, 미원에스씨, 신도리코, LG디스플레이,
    농심, 삼성E&A, 한솔홈데코, 한국수출포장공업, 신한서부티엔디리츠, 일진전기, 일진홀딩스, 일진하이솔루스, 일진다이아, SK리츠
  - 같은 규칙으로 빠진 곳(공식 연결 데이터나 계정 없음): 진양화학, 인천도시가스, 미원화학, 롯데리츠, 삼성카드, 롯데하이마트, 유화증권

## 결과 자세히

### 1. LLM이 실제로 낸 값 (`results/llm_items_*.json`)

모델: IBM Granite 3.3 8B(Ollama, 노트북 M2 Pro 16GB), 입력은 연결재무제표 중 재무상태표와 손익계산서 표(회사당 3,730~7,404 토큰), 출력 상한 2,500토큰,
회사당 한 번, 중앙값 약 80초. Claude는 이번에 쓰지 못했습니다(키 만료).

| | 개발용 10곳 | 처음 보는 20곳 | 합계 |
|---|---|---|---|
| 낸 값(같은 칸에 여러 값을 낸 것 포함) | 138 | 336 | 474 |
| 공식 금액과 맞은 값 | 67 | 164 | 231 |
| P1이 받은 값 / 그중 틀린 값 | 100 / 52 | 154 / 86 | 254 / 138 |
| P2가 받은 값 / 그중 틀린 값 | 44 / 0 | 66 / 0 | 110 / 0 |
| P1은 받았는데 P2가 거부한 맞는 값 | 4 | 2 | 6 |
| P2로 맞게 채운 칸 / 전체 칸 | 44 / 120 | 65 / 240 | 109 / 360 |

P1이 받은 틀린 값 138개를 P2가 거부한 이유: 기간 열의 숫자가 다름 65(전전기 숫자를 전기로 고른 경우 등), 행 이름이 계정과 다름 59, 인용이 가리킨 행이 다름 12, 없는 표나 행 2.
작은 모델이라 정답률이 낮고(49%), 출력 상한에 걸려 값을 못 낸 회사가 개발용에서 2곳 있었습니다. P2로 맞게 채운 칸이 30%라 나머지는 사람이 봐야 합니다.

### 2. 원문 읽기 (`results/parse*.json`)

코드는 공식 당기 금액이 든 행을 연결재무제표 표에서 찾습니다(당기가 맞는 것은 찾는 조건). 독립적으로 확인되는 것은 같은 행의 전기 칸(기간 열 판단)과 표 단위입니다.

| | 계정 | 찾은 계정 | 찾은 계정 중 전기도 공식 금액과 같음 |
|---|---|---|---|
| 개발용 10곳 | 60 | 60 | 60 |
| 처음 보는 20곳 | 120 | 119 | 119 |

놓친 1개는 GS피앤엘의 '매출액 및 지분법이익' 행으로, 정확히 비교하는 이름 목록에 없는 이름입니다. 표 단위는 원, 천원, 백만원이 섞여 있었습니다.

### 3. 오류 주입 (`results/inject_gold*.json`, 명세 시험)

정답 칸에서 코드로 만든 올바른 답에 오류를 하나씩 넣었습니다. 오답을 만드는 함수와 P2의 검사 함수가 같아서 P2의 0은 설계대로 나오는 값입니다.

| 넣은 오류(개발용 120개 기준) | P1이 받은 틀린 값 | P2 | P2, 이름을 부분 문자열로 비교할 때 |
|---|---|---|---|
| 당기와 전기 바꿔치기 | 120 | 0 | 0 |
| 연결 대신 별도재무제표 값 | 120 | 0 | 0 |
| 같은 표의 다른 행 | 119 | 0 | **29** |
| 단위를 잘못 적음 | 120 | 0(표 단위로 교정) | 0 |
| 숫자 한 자리 틀림 | 0 | 0 | 0 |

처음 만든 P2는 행 이름을 부분 문자열로 비교해서 '매출원가'를 매출액으로, '부채와자본총계'를 자본총계로 받았습니다(처음 보는 20곳에서는 238건 중 48건).
주석 번호를 뗀 이름이 계정별 목록과 정확히 같아야 받게 바꿨고, 그 대가가 위 GS피앤엘 1개입니다.

### 4. 검색 비교 (`results/recall*.json`)

정답 행이 BM25 상위 5개 덩어리에 든 계정 수. 검색어는 계정마다 고정 문구, 낱말은 글자 2개씩 끊어 셉니다.

| 덩어리 | 개발용(60) | 처음 보는 곳(119) |
|---|---|---|
| 행 단위, '재무에 관한 사항' 전체 | 31 | 53 |
| 1,000자 고정(200자 겹침), 전체 | 36 | 70 |
| 행 단위, 요약재무정보와 연결재무제표 섹션만 | 52 | 104 |

'재무에 관한 사항'에는 회사마다 표가 300~1,600개 있고 주석과 별도재무제표에도 '매출액' 행이 많습니다. 셋째 줄은 정답이 있는 섹션으로 범위를 줄인 것이라
청킹보다는 섹션 제목(메타데이터) 거름의 효과입니다.

### 5. 임베딩 검색 비교 (`results/recall_embed*.json`, 2026-11-19 추가)

같은 행 덩어리와 같은 검색어로 BM25, 임베딩, 둘을 합친 순위(RRF, k=60)를 비교했습니다. 숫자는 정답 행이 상위 5개에 든 계정 수입니다.

- 모델: [multilingual-e5-large-instruct](https://huggingface.co/intfloat/multilingual-e5-large-instruct)와, 그 모델을 한국어 STS 데이터로 미세조정해 공개한
  [PwC-Embedding_expr](https://huggingface.co/SamilPwC-AXNode-GenAI/PwC-Embedding_expr). 둘 다 0.56B, 최대 512토큰. 검색어 앞에 e5-instruct 형식의 지시문을 붙였습니다.
- 덩어리 두 가지. '행 전체'는 표 위치, 머리글(300자까지), 행(600자까지)을 이은 글입니다. '행 이름 먼저'는 행 이름을 맨 앞에 두고 숫자 칸을 뺀 글입니다.
- 순서: 처음에는 '행 전체'만 두고 개발용 10곳을 돌렸습니다. 임베딩이 BM25보다 크게 낮아 상위 결과를 열어 보니 같은 표의 행들이 위치와 머리글을 똑같이 갖고 있어
  표 제목 행이나 같은 표의 다른 행이 먼저 올라왔습니다. 그래서 '행 이름 먼저'를 추가했고, 처음 보는 20곳은 그 뒤에 한 번 돌렸습니다.

| 덩어리 | 방식 | 개발용 60, 전체 | 개발용 60, 섹션만 | 처음 보는 곳 119, 전체 | 처음 보는 곳 119, 섹션만 |
|---|---|---|---|---|---|
| 행 전체 | BM25 | 33 | 52 | 57 | 104 |
| 행 전체 | e5-large-instruct | 12 | 16 | 25 | 36 |
| 행 전체 | PwC-Embedding_expr | 13 | 18 | 22 | 38 |
| 행 이름 먼저 | BM25 | 32 | 50 | 60 | 104 |
| 행 이름 먼저 | e5-large-instruct | 42 | 51 | 85 | 101 |
| 행 이름 먼저 | PwC-Embedding_expr | 37 | 46 | 80 | 101 |
| 행 이름 먼저 | BM25 + e5 (RRF) | 42 | 52 | 81 | 103 |
| 행 이름 먼저 | BM25 + PwC (RRF) | 41 | 51 | 84 | 105 |

'전체'는 '재무에 관한 사항' 전체(개발용 30,365행, 처음 보는 곳 56,881행), '섹션만'은 요약재무정보와 연결재무제표 섹션(2,565행, 4,885행)입니다.
위 4번 표의 BM25(31, 53)와 숫자가 조금 다른 것은 머리글과 행을 자른 덩어리를 썼기 때문입니다.

읽은 것
- 행을 통째로 임베딩하면 BM25보다 낮았습니다. BM25는 모든 행에 공통인 낱말의 가중치를 낮추는데 임베딩은 그 공통 글에 끌려갔습니다.
- 행 이름을 앞에 두고 숫자를 빼면, 섹션을 거르지 않은 전체 범위에서 임베딩이 BM25보다 20~25개 더 찾았습니다. 섹션을 거른 뒤에는 상위 5개 기준으로 차이가 없고,
  상위 1개 기준으로는 임베딩이 더 높았습니다(처음 보는 곳 119개 중 BM25 57, e5 66, PwC 68).
- 범위를 줄이는 효과(BM25 60 → 104)가 검색 방식을 바꾸는 효과보다 여전히 컸습니다.
- 두 임베딩 모델의 차이는 작았습니다. 검색어가 계정 6개의 고정 문구뿐이고 질의가 119개라 어느 쪽이 낫다고 말할 수 있는 실험이 아닙니다.
  PwC-Embedding_expr는 문장 유사도(STS) 데이터로 미세조정한 모델이고 표의 행 검색은 그 모델이 겨냥한 과제가 아닙니다.
- 리랭커와 다른 임베딩 모델, 사람이 쓴 질문 형태의 검색어는 비교하지 않았습니다.

## 한계

- 계정 6개는 공식 데이터로도 받을 수 있는 숫자라, 이 실험은 정답이 있는 곳에서 검사기를 시험한 것입니다. 주석 표, 부문 정보, 분기보고서처럼
  머리글에 기간이 없거나 행 이름이 제각각인 표에서는 P2의 가정이 깨질 수 있습니다.
- P2는 표 단위를 코드가 읽으므로 단위를 잘못 읽으면 틀린 값을 그대로 내보냅니다. 원문 읽기 결과(위 2)로만 확인했습니다.
- LLM은 작은 로컬 모델 하나, 회사당 한 번입니다. 큰 모델에서는 오류의 양과 종류가 다를 수 있습니다.

## 실행

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m dartcheck.fetch 10                     # 개발용. DART 키: ~/.config/opendart/.env 의 DART_API_KEY
.venv/bin/python -m dartcheck.fetch 20 10 holdout          # 그다음 20곳
.venv/bin/python -m dartcheck.run parse                    # 원문 읽기(표본은 DARTCHECK_SAMPLE=holdout 으로 바꿈)
.venv/bin/python -m dartcheck.run inject                   # 오류 주입
.venv/bin/python -m dartcheck.run recall                   # 검색 비교
.venv/bin/pip install -r requirements-embed.txt && .venv/bin/python -m dartcheck.embed   # 임베딩 검색 비교(모델 약 3.4GB 내려받음, M2 Pro에서 약 1시간 반)
DARTCHECK_LOCAL_MODEL=granite3.3:8b DARTCHECK_MODES="연결 본표" DARTCHECK_WORKERS=1 .venv/bin/python -m dartcheck.run llm
.venv/bin/python -m dartcheck.run items results/llm_<시각>.json
```

내려받은 공시 원문은 `data/raw/`에만 두고 저장소에 넣지 않습니다. 원문 zip은 본문과 첨부 파일 여럿인데 opendart-py 0.1.0의 `get_document`는
전부 이어 붙인 글을 돌려주고 zip이 아닌 응답도 그대로 돌려줘서, 본문 파일만 쓰려고 클라이언트의 내부 요청 함수(`_request`)로 zip을 받습니다.
