@echo off
REM Tamla Data Agent launcher for Windows (installs packages on first run)
chcp 65001 > nul
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\pip install -r requirements.txt
)
if not exist .env copy .env.example .env
.venv\Scripts\streamlit run app.py
