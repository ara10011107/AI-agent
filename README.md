# 탐라 데이터 비서 (Tamla Data Agent)

제주빅데이터센터·제주데이터허브의 공공데이터를 **평소 말투로 질문하면**, AI 에이전트가 스스로
데이터를 찾고 → 구조를 살피고 → SQL·통계 계산을 하고 → 표·차트와 함께 쉬운 한국어 보고서로 답해 주는 서비스입니다.

[제주 AI·빅데이터 활용 AI Agent 개발 챌린지](https://daker.ai/public/hackathons/jeju-ai-bigdata-agent-challenge) 출품용으로 만들었습니다.
대회 목표인 "IT 전문가가 아닌 도민도 직접 실행해 데이터 탐색부터 분석 결과까지 얻는 서비스"에 맞춰 설계했습니다.

## 주요 기능

| 기능 | 설명 |
|---|---|
| 자연어 질문 | "애월읍에서 카페를 하려는데 요일별 매출이 어때?"처럼 질문 |
| 자동 데이터 탐색 | 데이터 목록·컬럼·값 형태를 먼저 확인하고 분석 계획을 세움 |
| SQL·통계 분석 | DuckDB SQL로 집계, 파이썬(pandas)으로 증감률·비중·상관 계산 |
| 시각화 | 결과를 plotly 차트와 표로 화면에 표시 |
| 쉬운 보고서 | 결론 → 근거 → 활용 제안 → 주의점 → 후속 질문 순서로 설명 |
| 재현성 | 실행한 SQL·코드를 화면과 보고서 부록, `runs/` 실행 기록(JSON)에 모두 남김 |
| 공공데이터 친화 | CP949/EUC-KR CSV, 엑셀 자동 인식, 한글 파일·컬럼 이름 그대로 사용 |
| 업로드 | 화면에서 CSV/엑셀을 올리면 바로 분석 대상에 추가 |

## 빠른 시작

### 1) 가장 쉬운 방법
- Windows: `run.bat` 더블클릭
- macOS/Linux: 터미널에서 `./run.sh`

처음 실행할 때 필요한 프로그램을 설치하고 브라우저에 화면이 열립니다. 왼쪽 설정에 Claude API 키를 넣으면 바로 쓸 수 있습니다.
(Python 3.10 이상이 설치되어 있어야 합니다.)

### 2) 직접 설치
```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # 파일을 열어 ANTHROPIC_API_KEY 입력 (선택)
streamlit run app.py               # 웹 화면
python cli.py "관광객 소비가 가장 많은 읍면동은?"   # 터미널 실행
```

### 3) 실제 제주 데이터 넣기
1. [제주데이터허브](https://www.jejudatahub.net) 또는 [제주빅데이터센터](https://bigdata.jeju.go.kr)에서 CSV/엑셀을 내려받습니다.
2. `data/` 폴더에 넣거나 화면 왼쪽 "데이터 추가"에서 업로드합니다.
3. (권장) `data/catalog.yaml`에 제목·출처·컬럼 설명을 적으면 분석 정확도가 올라갑니다.
4. 실제 데이터만 쓰려면 화면에서 "데모용 가상 샘플 데이터 포함"을 끄거나 `python cli.py --no-samples`를 사용합니다.

자세한 사용법은 [docs/MANUAL.md](docs/MANUAL.md), 기획서 초안은 [docs/PROPOSAL.md](docs/PROPOSAL.md)를 보세요.

> `data/sample/`의 파일은 화면 시연용 **가상(합성) 데이터**입니다. 실제 통계가 아니므로 제출물의
> 분석 결과에는 반드시 제주빅데이터센터·제주데이터허브의 실제 데이터를 사용하세요. 에이전트도 샘플을 쓰면 답변에 이 사실을 밝힙니다.

## 구조

```
app.py               웹 화면 (Streamlit)
cli.py               터미널 실행
agent/
  core.py            에이전트 루프 (Claude API 도구 호출 반복)
  tools.py           도구: list_datasets / describe_dataset / run_sql / run_python
  catalog.py         CSV·엑셀 적재, 인코딩 자동 판별, DuckDB 등록, 데이터 프로파일
  prompts.py         시스템 프롬프트 (분석 절차·답변 형식·원칙)
  report.py          HTML 보고서와 실행 기록 저장
data/
  catalog.yaml       데이터셋 설명서
  sample/            데모용 가상 데이터
scripts/make_sample_data.py   가상 데이터 생성
tests/               API 키 없이 돌아가는 테스트 (pytest)
```

### 동작 방식

```
사용자 질문
   │
   ▼
Claude (claude-opus-5-5, adaptive thinking)
   │  ① list_datasets      어떤 데이터가 있는지
   │  ② describe_dataset   컬럼·값 형태 확인
   │  ③ run_sql            집계·필터 (DuckDB, 읽기 전용)
   │  ④ run_python         통계·차트 (pandas, plotly)
   │  ⑤ 결과 검증 후 반복
   ▼
보고서 (결론 / 근거 / 활용 제안 / 주의점 / 후속 질문) + 표·차트 + 재현용 SQL·코드
```

- 모델: 기본 `claude-opus-5-5`, `AGENT_MODEL`로 변경 가능. 분석 깊이는 `AGENT_EFFORT`(기본 `medium`, 더 꼼꼼하게는 `high`).
- 안전 분류기가 요청을 거절할 때 서버가 권장 모델로 자동 재시도하도록 `fallbacks: "default"`를 켜 두었습니다.
- 대화 기록을 유지해 "그럼 서귀포시는?" 같은 후속 질문도 이어서 분석합니다.

## 안전·제약
- SQL은 읽기 전용이며 DuckDB의 외부 파일 접근을 막아 등록된 데이터만 조회합니다.
- `run_python`은 AI가 작성한 파이썬 코드를 **사용자 컴퓨터에서 그대로 실행**합니다(샌드박스 아님). 본인 PC에서 본인 데이터로만 사용하세요.
- 사용 요금: Claude API 사용량만큼 과금됩니다. 질문 1건당 도구 호출 수에 따라 달라지며, `AGENT_EFFORT=low`로 줄일 수 있습니다.

## 테스트
```bash
pytest           # 데이터 적재, 인코딩, SQL 안전장치, 파이썬 실행, 에이전트 루프(가짜 응답) 검증
```
