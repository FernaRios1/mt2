import os


def check_password(pwd):
    real = os.environ.get("APP_PASSWORD")
    if not real:
        return True  # sin contraseña configurada -> sin protección (se avisa en la UI)
    return pwd == real


def password_configured():
    return bool(os.environ.get("APP_PASSWORD"))
