import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

from agent import IntelligentAgent, OfflineAgent
from memory import init_db


def main() -> None:
    load_dotenv()
    mode = os.getenv("AGENT_MODE", "offline").strip().lower()
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()

    init_db()

    if mode == "online":
        if not api_key:
            raise RuntimeError("Falta OPENAI_API_KEY en .env para modo online")
        if OpenAI is None:
            raise RuntimeError("No está instalado el paquete openai para modo online")
        client = OpenAI(api_key=api_key)
        agent = IntelligentAgent(client=client, model=model)
        print("Agente iniciado en modo online.")
    else:
        agent = OfflineAgent()
        print("Agente iniciado en modo gratuito offline.")

    print("Escribe 'salir' para terminar.")

    while True:
        user_text = input("\nTu: ").strip()
        if not user_text:
            continue
        if user_text.lower() in {"salir", "exit", "quit"}:
            print("Hasta luego.")
            break

        answer = agent.chat(user_text)
        print(f"\nAgente: {answer}")


if __name__ == "__main__":
    main()
