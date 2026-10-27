#!/usr/bin/env bash
# 탐라 데이터 비서 실행 (macOS/Linux). 처음 실행 시 필요한 프로그램을 자동으로 설치합니다.
set -e
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
[ -f .env ] || cp .env.example .env
.venv/bin/streamlit run app.py
