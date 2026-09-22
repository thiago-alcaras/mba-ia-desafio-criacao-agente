"""Execute somente com a API parada. Nunca altera dados/."""
from .database import RUNTIME_DIR, ensure_database, reset_database


def reset():
    reset_database()
    for suffix in ("", "-wal", "-shm"):
        (RUNTIME_DIR / ("adk_sessions.sqlite3" + suffix)).unlink(missing_ok=True)
    ensure_database()


if __name__ == "__main__":
    reset()
    print("Dados e sessões do Residencial Aurora restaurados.")
