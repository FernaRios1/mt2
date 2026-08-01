"""Acceso simple por contraseña, con opción de restringir por IP.

Cómo configurar en Railway (Variables del servicio):
  APP_PASSWORD=<una contraseña>              -- obligatoria
  ALLOWED_IPS=1.2.3.4,5.6.7.8                -- opcional, IPs públicas de Imperial

Si Imperial tiene una IP pública fija para la oficina (pregúntale a tu proveedor
de internet o a IT), agrégala en ALLOWED_IPS y el panel queda cerrado incluso
con la contraseña correcta si alguien entra desde otro lugar. Si no la tienes
todavía, deja ALLOWED_IPS vacío y usa solo la contraseña por ahora.
"""
import os
import streamlit as st


def _client_ip():
    # Railway/la mayoría de los proxies entregan la IP real en este header.
    try:
        headers = st.context.headers
        fwd = headers.get("X-Forwarded-For", "")
        if fwd:
            return fwd.split(",")[0].strip()
    except Exception:
        pass
    return None


def gate():
    """Llamar al principio de cada página. Detiene la ejecución si no pasa."""
    allowed_ips = [ip.strip() for ip in os.environ.get("ALLOWED_IPS", "").split(",") if ip.strip()]
    if allowed_ips:
        ip = _client_ip()
        if ip not in allowed_ips:
            st.error("Acceso restringido a la red de Imperial. Tu conexión no está autorizada.")
            st.stop()

    password = os.environ.get("APP_PASSWORD")
    if not password:
        st.warning("APP_PASSWORD no está configurada — el panel queda sin protección. "
                    "Agrégala en las variables del servicio en Railway.")
        return

    if st.session_state.get("autenticado"):
        return

    st.title("Rentabilidad Rack — Imperial")
    pwd = st.text_input("Contraseña", type="password")
    if st.button("Entrar"):
        if pwd == password:
            st.session_state["autenticado"] = True
            st.rerun()
        else:
            st.error("Contraseña incorrecta.")
    st.stop()
