@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=python
where py >nul 2>nul && set PY=py
%PY% --version >nul 2>nul || (echo Сначала установите Python с python.org и отметьте галочку "Add Python to PATH". & pause & exit /b)
if not exist .venv %PY% -m venv .venv
call .venv\Scripts\activate.bat
echo Устанавливаю нужные библиотеки...
python -m pip install -q -r requirements.txt
echo.
echo Бот запускается. Не закрывайте это окно, пока хотите, чтобы бот работал.
python run.py
pause
