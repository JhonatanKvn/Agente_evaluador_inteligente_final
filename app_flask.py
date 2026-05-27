"""Compatibilidad: punto de entrada antiguo.

Ejecuta la app Flask delegando al nuevo modulo organizado.
"""

from app.web.server import run


if __name__ == "__main__":
    run()

