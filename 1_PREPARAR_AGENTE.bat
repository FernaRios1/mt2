@echo off
setlocal
cd /d "%~dp0"
echo Instalando dependencias del agente...
python -m pip install -r requirements_agente.txt
if errorlevel 1 (
  echo.
  echo ERROR: no se pudieron instalar las dependencias.
  pause
  exit /b 1
)
echo.
echo Listo. Ahora configura SQLSERVER_DSN y DATABASE_URL como variables de entorno de Windows.
echo Despues puedes ejecutar 2_ACTUALIZAR_DATA.bat.
pause
