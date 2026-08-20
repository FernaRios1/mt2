@echo off
setlocal
cd /d "%~dp0"

if "%SQLSERVER_DSN%"=="" (
  echo ERROR: falta la variable SQLSERVER_DSN.
  echo Configurala en Windows y vuelve a abrir esta ventana.
  pause
  exit /b 1
)
if "%DATABASE_URL%"=="" (
  echo ERROR: falta la variable DATABASE_URL.
  echo Configurala en Windows y vuelve a abrir esta ventana.
  pause
  exit /b 1
)

echo ============================================
echo Actualizando Desempeno de Racks...
echo ============================================
python agente_rentabilidad_rack.py
if errorlevel 1 (
  echo.
  echo ERROR: la sincronizacion fallo. Revisa el mensaje de arriba.
  pause
  exit /b 1
)

echo.
echo Datos actualizados correctamente.
echo En la web, recarga la pagina para ver la nueva sincronizacion.
pause
