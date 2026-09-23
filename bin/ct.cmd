@echo off
setlocal
set "CT_VENV_PYTHON=%USERPROFILE%\venvs\.venv_codetools\Scripts\python.exe"
if exist "%CT_VENV_PYTHON%" (
  "%CT_VENV_PYTHON%" "%~dp0..\ct.py" %*
) else (
  python "%~dp0..\ct.py" %*
)
