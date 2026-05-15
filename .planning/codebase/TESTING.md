# Testing Patterns

**Analysis Date:** 2026-05-12

## Test Framework

**Runner:**
- Not configured — no `pytest`, `unittest`, or any test runner is installed or configured
- No `pytest.ini`, `pyproject.toml`, `setup.cfg`, or `conftest.py` found

**Assertion Library:**
- Not applicable — no test framework in place

**Run Commands:**
```bash
# Manual DB connectivity check (only existing verification)
python test_db.py

# No test commands exist. When a framework is added, expected commands:
# pytest                          # Run all tests
# pytest tests/                   # Run from tests directory
# pytest -x                       # Stop on first failure
# pytest --cov=app                # Coverage (once configured)
```

## Test File Organization

**Location:**
- No test directory exists
- The only verification script is `test_db.py` at project root (not in a `tests/` directory)

**Naming:**
- No convention established — `test_db.py` is a standalone script, not a framework test module

**Current Structure:**
```
iot_backend/
├── app/
│   ├── crud.py
│   ├── database.py
│   ├── main.py
│   ├── mock_esp32.py
│   ├── models.py
│   ├── mqtt_listener.py
│   └── schemas.py
├── test_db.py          # Only verification script (manual, not automated)
└── schema_iot.sql
```

**Recommended structure when adding tests:**
```
iot_backend/
├── app/
│   └── ...
├── tests/
│   ├── __init__.py
│   ├── conftest.py           # Fixtures: async db session, test client, MQTT mock
│   ├── test_main.py          # Endpoint integration tests
│   ├── test_crud.py          # CRUD function unit tests
│   ├── test_schemas.py       # Pydantic validation tests
│   ├── test_mqtt_listener.py # MQTT message processing tests
│   └── fixtures/
│       └── sample_data.py   # Test data factories
└── schema_iot.sql
```

## Test Structure

**Suite Organization:**
- No suites exist. When adding, follow the pattern:

```python
# tests/test_crud.py — Example structure
import pytest
from app.crud import crear_telemetria, obtener_telemetria_por_mac

@pytest.mark.asyncio
async def test_crear_telemetria_returns_none_for_unknown_mac(db_session):
    telemetria_in = TelemetriaCreate(
        mac_dispositivo="00:00:00:00:00:00",
        voltaje=12.0,
        corriente=1.5,
        potencia=18.0,
        tiempo_operacion_s=100
    )
    result = await crear_telemetria(db_session, telemetria_in)
    assert result is None
```

**Patterns:**
- No setup/teardown patterns established — need `conftest.py` with async fixtures
- All CRUD functions are async — tests must use `pytest-asyncio` or equivalent
- FastAPI `TestClient` should wrap `app` from `app/main.py`

## Mocking

**Framework:** Not configured — no `pytest-mock`, `unittest.mock`, or similar

**Patterns needed for this codebase:**

```python
# 1. Mocking the database session
# Create a test session factory that uses an in-memory SQLite or test MariaDB
@pytest.fixture
async def db_session():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as session:
        yield session
        await session.rollback()

# 2. Mocking MQTT publish in endpoints
from unittest.mock import patch, AsyncMock

@pytest.mark.asyncio
async def test_comando_estado_publishes_mqtt(client):
    with patch("paho.mqtt.publish.single") as mock_publish:
        response = client.post("/api/comando/estado", json={
            "mac_dispositivo": "00:1B:44:11:3A:B7",
            "encendido": True
        })
        mock_publish.assert_called_once()

# 3. Mocking MQTT listener for endpoint tests
@pytest.fixture
def client_without_mqtt():
    # Bypass lifespan that starts MQTT listener
    app = FastAPI()
    # Register routes without the MQTT lifespan
    ...
```

**What to Mock:**
- `paho.mqtt.publish.single()` — in endpoint tests (command endpoints publish over MQTT)
- `paho.mqtt.client.Client` — when testing `mqtt_listener.py` independently
- `AsyncSessionLocal` — in MQTT processing tests
- Database engine — use SQLite or test-container MariaDB for integration tests

**What NOT to Mock:**
- Pydantic schemas — test validation directly
- CRUD functions when testing endpoints — verify E2E behavior with a real (test) DB

## Fixtures and Factories

**Test Data:**
- No fixtures or factories exist. When adding, use the Pydantic schemas as a basis:

```python
# tests/fixtures/sample_data.py
from app.schemas import TelemetriaCreate, DispositivoEstado

def sample_telemetria_create(mac="00:1B:44:11:3A:B7", **overrides):
    defaults = {
        "mac_dispositivo": mac,
        "voltaje": 12.0,
        "corriente": 1.5,
        "potencia": 18.0,
        "tiempo_operacion_s": 3600,
    }
    defaults.update(overrides)
    return TelemetriaCreate(**defaults)

def sample_dispositivo_estado(mac="00:1B:44:11:3A:B7", encendido=True, **overrides):
    defaults = {"mac_dispositivo": mac, "encendido": encendido}
    defaults.update(overrides)
    return DispositivoEstado(**defaults)
```

**Key MAC address for testing:**
- `mock_esp32.py` uses `MAC_ESP32 = "00:1B:44:11:3A:B7"` — use this as a known test device MAC

**Location:**
- Recommended: `tests/fixtures/` directory with `sample_data.py`

## Database Testing Strategy

**Challenge:** The ORM models depend on MariaDB-specific features (range partitioning, `INSERT` with auto-increment on composite PK). Testing approaches:

1. **Unit tests:** Use SQLite in-memory for basic CRUD logic. Schema must be created without `PARTITION BY` clause — use a separate test DDL or conditional model creation.

2. **Integration tests:** Use a Docker MariaDB container with `schema_iot.sql` for full compatibility testing.

3. **The `Telemetria` model composite PK `(id, timestamp)`** requires both fields. Tests creating `Telemetria` instances must provide or auto-generate both.

**Fixture approach:**
```python
# tests/conftest.py
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from app.database import Base

# SQLite for unit tests (no partition support needed)
TEST_DATABASE_URL = "sqlite+aiosqlite:///./test.db"

@pytest.fixture
async def engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()

@pytest.fixture
async def db_session(engine):
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        yield session
        await session.rollback()
```

**Note on `Telemetria` model:** The `PARTITION BY RANGE` clause in `schema_iot.sql` is MariaDB-specific and will fail on SQLite. For SQLite unit tests, the ORM `Telemetria` model will work, but the composite PK `(id, timestamp)` requires `id` to auto-increment. SQLite handles this differently from MariaDB.

## Coverage

**Requirements:** None enforced — no coverage tool configured

**View Coverage:**
```bash
# When pytest-cov is configured:
pytest --cov=app --cov-report=term-missing
pytest --cov=app --cov-report=html        # Open htmlcov/index.html
```

**Recommended coverage targets:**
- `app/crud.py` — high priority (all data access logic)
- `app/main.py` — high priority (all API endpoints)
- `app/schemas.py` — medium priority (validation rules)
- `app/mqtt_listener.py` — high priority (async/thread bridging, known bug)
- `app/models.py` — low priority (declarative, few logic paths)

## Test Types

**Unit Tests:**
- Schema validation: verify `TelemetriaCreate` rejects invalid data (negative voltaje, bad MAC format)
- CRUD functions: `crear_telemetria`, `obtener_telemetria_por_mac`, `actualizar_estado_dispositivo`, `obtener_estado_dispositivo`
- All must use `pytest.mark.asyncio` since all CRUD functions are `async`

**Integration Tests:**
- API endpoints via `TestClient` from `starlette.testclient`
- Full request/response cycle including DB reads/writes
- MQTT publish verification (mock `paho.mqtt.publish.single`)

**E2E Tests:**
- Not currently in scope
- The `mock_esp32.py` script serves as a manual integration test tool — it starts an MQTT subscriber that publishes telemetry and listens for commands

## Common Patterns

**Async Testing:**

```python
# Required: pytest-asyncio for async test functions
import pytest

@pytest.mark.asyncio
async def test_obtener_telemetria_por_mac_returns_empty_for_unknown(db_session):
    result = await obtener_telemetria_por_mac(db_session, mac="FF:FF:FF:FF:FF:FF")
    assert result == []
```

**Error Testing:**

```python
from fastapi import status
from httpx import AsyncClient  # For async test client

@pytest.mark.asyncio
async def test_registrar_telemetria_unknown_device_returns_404(client):
    response = await client.post("/api/telemetria", json={
        "mac_dispositivo": "FF:FF:FF:FF:FF:FF",
        "voltaje": 12.0,
        "corriente": 1.5,
        "potencia": 18.0,
        "tiempo_operacion_s": 100
    })
    assert response.status_code == status.HTTP_404_NOT_FOUND
```

**Pydantic Validation Testing:**

```python
import pytest
from pydantic import ValidationError
from app.schemas import TelemetriaCreate

def test_telemetria_create_rejects_negative_voltage():
    with pytest.raises(ValidationError):
        TelemetriaCreate(
            mac_dispositivo="00:1B:44:11:3A:B7",
            voltaje=-1.0,   # ge=0 should reject
            corriente=1.5,
            potencia=18.0,
            tiempo_operacion_s=100
        )

def test_telemetria_create_rejects_bad_mac_length():
    with pytest.raises(ValidationError):
        TelemetriaCreate(
            mac_dispositivo="00:1B:44",  # min_length=17
            voltaje=12.0,
            corriente=1.5,
            potencia=18.0,
            tiempo_operacion_s=100
        )
```

## Existing Verification Tool

**`test_db.py`** at project root:
- standalone async script, NOT a pytest test
- validates MariaDB connectivity by executing `SELECT VERSION()`
- uses `app.database.engine` directly
- uses `asyncio.run()` as the entrypoint
- **Not suitable as a CI test** — it requires a live database connection with correct credentials

```python
# Current pattern from test_db.py
async def probar_conexion():
    async with engine.begin() as conn:
        resultado = await conn.execute(text("SELECT VERSION();"))
        version = resultado.scalar()
        print(f"¡Éxito! Conectado a MariaDB. Versión: {version}")
```

## Known Bug Test Cases

The following scenarios should be covered when a test framework is added:

1. **`mqtt_listener.py:63` semantic bug:** The `conexion` handler calls `actualizar_estado_dispositivo(db, mac, encendido=estado_bool)` which sets `is_encendido` instead of `is_online`. Test should verify that an MQTT connection message updates `is_online`, not `is_encendido`.

2. **Hardcoded MQTT credentials in `main.py:56,74`:** Credentials should be read from environment variables, matching the pattern in `mqtt_listener.py`.

3. **`DispositivoLimites` partial update:** `model_dump(exclude_unset=True)` should only send fields the user actually changed — this needs verification.

## Recommended Testing Setup

**Framework:** `pytest` with `pytest-asyncio` for async support

**Install:**
```bash
pip install pytest pytest-asyncio pytest-cov aiosqlite httpx
```

**Configuration:** Add to root `pyproject.toml` or `pytest.ini`:
```ini
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

---

*Testing analysis: 2026-05-12*