#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ -z "${SQLSERVER_DSN:-}" ]; then
  echo "ERROR: falta SQLSERVER_DSN"
  exit 1
fi
if [ -z "${DATABASE_URL:-}" ]; then
  echo "ERROR: falta DATABASE_URL"
  exit 1
fi
python3 agente_rentabilidad_rack.py
