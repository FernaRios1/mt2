import os
import hmac
import streamlit as st


def gate():
    """Protege la aplicación usando la variable APP_PASSWORD."""

    password_correcta = os.getenv("APP_PASSWORD", "").strip()

    if not password_correcta:
        st.error("Falta configurar APP_PASSWORD.")
        st.stop()

    if st.session_state.get("autenticado", False):
        return

    st.title("🔐 Acceso al panel")

    password_ingresada = st.text_input(
        "Contraseña",
        type="password",
        key="password_acceso",
    )

    if st.button("Ingresar", type="primary"):
        if hmac.compare_digest(password_ingresada, password_correcta):
            st.session_state["autenticado"] = True
            st.rerun()
        else:
            st.error("Contraseña incorrecta.")

    st.stop()