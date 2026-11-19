@echo off
title ֱ���ݲ���
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [1/3] ���ڴ����������л���...
  python -m venv .venv
  if errorlevel 1 goto nopython
)

".venv\Scripts\python.exe" -c "import fastapi, uvicorn, sounddevice, webview" >nul 2>&1
if errorlevel 1 (
  echo [2/3] �״����У����ڰ�װ���������Ե�...
  ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
  if errorlevel 1 (
    echo       ����װʧ�ܣ����ùٷ�Դ����...
    ".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
    if errorlevel 1 goto depfail
  )
)

echo [3/3] ��������ֱ���ݲ���...
".venv\Scripts\python.exe" -m app.launcher
if errorlevel 1 pause
exit /b 0

:nopython
echo.
echo û���ҵ� Python�����Ȱ�װ Python 3.10 ���ϰ汾����װʱ��ѡ Add python.exe to PATH��
pause
exit /b 1

:depfail
echo.
echo ������װʧ�ܣ�����������������С�
pause
exit /b 1
