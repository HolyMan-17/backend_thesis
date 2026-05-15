# Technology Stack

**Analysis Date:** 2026-05-12

## Languages

**Primary:**
- Python 3.12.3 — All backend logic, API endpoints, MQTT listener, database operations, and test scripts

**Secondary:**
- SQL (MariaDB dialect) — Schema DDL in `schema_iot.sql`, including partitioning definitions

## Runtime

**Environment:**
- CPython 3.12.3 (virtual environment at `/home/manu0ak/iot_backend/venv/`)
- Venv config: `/home/manu0ak/iot_backend/venv/pyvenv.cfg`
- System packages excluded (`include-system-site-packages = false`)

**Package Manager:**
- pip (inside venv)
- Lockfile: **Missing** — no `requirements.txt`, `Pipfile`, `pyproject.toml`, or `poetry.lock` exists

## Frameworks

**Core:**
- FastAPI 0.136.1 — HTTP REST API framework; app defined in `app/main.py`
- Starlette 1.0.0 — Underlying ASGI toolkit for FastAPI
- Uvicorn 0.46.0 — ASGI server; runs `app.main:app`

**Database:**
- SQLAlchemy 2.0.49 — ORM and async query builder
- aiomysql 0.3.2 — Async MySQL/MariaDB driver (dialect `mysql+aiomysql`)
- PyMySQL 1.1.3 — Synchronous MySQL fallback (used by test script)

**Messaging:**
- paho-mqtt 2.1.0 — MQTT v3/v5 client library for subscriber (`app/mqtt_listener.py`) and publisher (`app/main.py`, `app/mock_esp32.py`)

**Serialization & Validation:**
- Pydantic 2.13.3 (pydantic_core 2.46.3) — Request/response schemas in `app/schemas.py`

**Configuration:**
- python-dotenv 1.2.2 — Loads `.env` from project root in `app/database.py`

## Key Dependencies

**Critical:**
- SQLAlchemy 2.0.49 — Async ORM engine (`create_async_engine`), session management (`AsyncSession`), declarative models (`declarative_base`)
- aiomysql 0.3.2 — Async DB driver; connection string `mysql+aiomysql://...` in `app/database.py:22`
- paho-mqtt 2.1.0 — Background MQTT listener thread (`client.loop_start()` in `app/mqtt_listener.py:89`) and synchronous publish (`publish.single` in `app/main.py:57,75`)
- Pydantic 2.13.3 — Schema validation with `BaseModel`, `Field` constraints, `model_dump(exclude_unset=True)` for partial updates

**Infrastructure:**
- Uvicorn 0.46.0 — ASGI server with `--reload` for development
- anyio 4.13.0 — Async I/O abstraction (uvicorn/starlette dependency)
- typing_extensions 4.15.0 — Backported type hints
- idna 3.13 — Internationalized domain name handling

## Configuration

**Environment:**
- `.env` file at `/home/manu0ak/iot_backend/.env` — contains database and MQTT credentials (existence confirmed; contents not read per policy)
- Loaded via `python-dotenv` in `app/database.py:7` with explicit path resolution:
  ```python
  load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))
  ```

**Required env vars** (defined in `app/database.py:10-14` and `app/mqtt_listener.py:13-14`):
- `DB_USER` — MariaDB username
- `DB_PASSWORD` — MariaDB password
- `DB_HOST` — MariaDB host
- `DB_PORT` — MariaDB port
- `DB_NAME` — MariaDB database name
- `MQTT_USER` — MQTT broker username (defaults to `"esp-gateway"`)
- `MQTT_PASS` — MQTT broker password (defaults to hardcoded value — see concern)

**Startup validation:** `app/database.py:17-18` raises `ValueError` if `DB_USER`, `DB_PASSWORD`, or `DB_NAME` are missing.

**Build:**
- No build system configured — no `pyproject.toml`, `setup.py`, or `Makefile`
- No linter or formatter configuration detected (no `.eslintrc`, `pyproject.toml` tool sections, `ruff.toml`, or `black.toml`)
- No test runner configuration detected

**Run commands** (from AGENTS.md):
```bash
source venv/bin/activate
uvicorn app.main:app --reload          # Start API server
python test_db.py                       # Test DB connectivity
python -m app.mock_esp32                # Simulate ESP32 device
```

## Platform Requirements

**Development:**
- Python 3.12+ with venv
- MariaDB server accessible at configured `DB_HOST:DB_PORT`
- Mosquitto MQTT broker running locally at `127.0.0.1:1883`
- Network access to MariaDB and MQTT broker

**Production:**
- Linux VPS (implied by AGENTS.md reference to VPS)
- MariaDB with InnoDB engine and range partitioning support
- Mosquitto MQTT broker (local to application server)
- Uvicorn as ASGI server (likely behind a reverse proxy in production)

## Special Notes

**No `app/__init__.py`:** The `app/` directory lacks an `__init__.py` file. All imports use the `app.` package prefix (e.g., `from app.database import ...`). This works because uvicorn resolves the package from the project root. Adding `__init__.py` is not required but would make the package importable outside uvicorn.

**No dependency manifest:** There is no `requirements.txt` or `pyproject.toml`. The venv is the only record of installed packages. Dependency reproduction requires:
```bash
pip install fastapi uvicorn sqlalchemy aiomysql pymysql paho-mqtt python-dotenv pydantic
```

**Spanish naming convention:** All models, tables, columns, and endpoint paths use Spanish. Key translations documented in `AGENTS.md`.

**MariaDB partitioning:** The `telemetria` table uses `PARTITION BY RANGE` on `YEAR(timestamp) * 100 + MONTH(timestamp)`. Partitions pre-created through 2026-12 with a `p_max` catch-all. See `schema_iot.sql:100-109`.

---

*Stack analysis: 2026-05-12*