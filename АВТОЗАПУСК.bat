@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\pythonw.exe" (
  echo Сначала запустите бота один раз ярлыком «Запустить бота», а потом откройте этот файл.
  pause
  exit /b
)
powershell -NoProfile -Command "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) 'Matesha bot.lnk')); $s.TargetPath=(Join-Path (Get-Location) '.venv\Scripts\pythonw.exe'); $s.Arguments='run.py'; $s.WorkingDirectory=(Get-Location).Path; $s.Save()"
echo.
echo Готово: теперь бот запускается сам при включении компьютера, без окна.
echo Чтобы выключить автозапуск: Win+R, напишите shell:startup, удалите ярлык Matesha bot.
pause
