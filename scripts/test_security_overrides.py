"""
Test Security Overrides: AI Auto-Kill, BMS Emergency, and Critical Threshold Limits.
Validates that these events correctly disable schedule automation and update messages.
Uses SQLite in-memory for local testing.

NOTE: SQLite doesn't support SELECT ... FOR UPDATE, so we mock the for_update() calls
by monkey-patching the SQLAlchemy query builder to no-op for_update in the test context.
"""
import asyncio
import os
import sys
import json
from datetime import datetime, timezone, time, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import Integer, select, event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, selectinload, Query

# Setup path for app imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import Base
from app.models import (
    Usuario, Artefacto, ArtefactoLimite, ArtefactoHorario,
    EventoUsuario, AlertaSistema, PermisoUsuarioArtefacto,
    Telemetria, Recomendacion, NotificacionUsuario,
)

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

# Monkey-patch Select.with_for_update to be a no-op for SQLite compatibility
from sqlalchemy.sql import Select
_original_with_for_update = Select.with_for_update
def _noop_with_for_update(self, *args, **kwargs):
    return self  # just return self without adding FOR UPDATE clause
Select.with_for_update = _noop_with_for_update


async def setup_database():
    """Create an in-memory SQLite DB with the required tables."""
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)

    # SQLite requires INTEGER (not BIGINT) for autoincrement PKs
    EventoUsuario.__table__.c.id.type = Integer()
    AlertaSistema.__table__.c.id.type = Integer()
    Recomendacion.__table__.c.id.type = Integer()
    NotificacionUsuario.__table__.c.id.type = Integer()

    # Create all tables EXCEPT Telemetria (which has composite PK incompatible with SQLite)
    tables_to_create = [
        Usuario.__table__,
        Artefacto.__table__,
        ArtefactoLimite.__table__,
        ArtefactoHorario.__table__,
        EventoUsuario.__table__,
        AlertaSistema.__table__,
        PermisoUsuarioArtefacto.__table__,
        Recomendacion.__table__,
        NotificacionUsuario.__table__,
    ]
    async with engine.begin() as conn:
        for table in tables_to_create:
            await conn.run_sync(table.create)

        # Create Telemetria manually with single PK (id) to avoid composite PK error
        from sqlalchemy import text
        await conn.execute(text("""
            CREATE TABLE telemetria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                id_artefacto INTEGER NOT NULL,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                voltaje NUMERIC(8, 2) NOT NULL,
                corriente NUMERIC(8, 2) NOT NULL,
                potencia NUMERIC(8, 2) NOT NULL,
                tiempo_operacion_s INTEGER NOT NULL,
                ai_status INTEGER DEFAULT 0 NOT NULL,
                estado_sin_cambios BOOLEAN DEFAULT 0 NOT NULL
            )
        """))

    TestSession = sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False,
    )
    return engine, TestSession


async def seed_device_with_schedule(db: AsyncSession, mac: str, name: str, prioridad: str = "P2"):
    """Create an online device with an active schedule, limits, user, and permission."""
    device = Artefacto(
        mac=mac,
        nombre_personalizado=name,
        nivel_prioridad=prioridad,
        estado_deseado=True,
        estado_reportado=True,
        is_online=True,
    )
    db.add(device)
    await db.commit()
    await db.refresh(device)

    limites = ArtefactoLimite(
        id_artefacto=device.id,
        limite_consumo_w=100,
        limite_voltaje=130.0,
        limite_corriente=15.0,
        limite_potencia=200.0,
    )
    db.add(limites)

    schedule = ArtefactoHorario(
        id_artefacto=device.id,
        dias_operacion=[1, 2, 3, 4, 5, 6, 7],
        hora_encendido=time(0, 0),
        hora_apagado=time(23, 59),
        automatizacion_activa=True,
    )
    db.add(schedule)

    user = Usuario(
        auth0_id=f"test|{mac}",
        email=f"{mac}@test.com",
        nombre="Test User",
        ai_control_habilitado=True,
        auto_apagado_low_priority=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    perm = PermisoUsuarioArtefacto(
        id_usuario=user.id,
        id_artefacto=device.id,
        nivel_acceso="ADMIN",
    )
    db.add(perm)
    await db.commit()

    return device, user


async def test_ai_auto_kill_disables_schedule():
    """Test 1: AI Auto-Kill execution disables active schedule automation."""
    print("\n--- TEST 1: AI Auto-Kill Disables Schedule ---")
    engine, TestSession = await setup_database()

    import app.recommendation_engine as rec_engine
    rec_engine.AsyncSessionLocal = TestSession
    rec_engine._publish_mqtt = AsyncMock()
    rec_engine._broadcast_event = AsyncMock()
    rec_engine.enviar_push_a_duenos = AsyncMock()

    async with TestSession() as db:
        device, user = await seed_device_with_schedule(db, "AA:BB:CC:DD:EE:01", "AI Kill Test")

        # Set auto_kill_at in the past so it triggers immediately
        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        device.auto_kill_at = past
        await db.commit()

        device_id = device.id

    # Insert RISKY telemetry for the evaluator
    async with TestSession() as db:
        stmt = select(Artefacto).where(Artefacto.mac == "AA:BB:CC:DD:EE:01")
        result = await db.execute(stmt)
        dev = result.scalar_one()

        for i in range(5):
            t = Telemetria(
                id_artefacto=dev.id,
                voltaje=120.0,
                corriente=5.0,
                potencia=60.0,
                tiempo_operacion_s=i * 60,
                ai_status=1,
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=5 - i),
            )
            db.add(t)
        await db.commit()

    await rec_engine.scan_all_devices()

    # Verify results
    async with TestSession() as db:
        stmt = (
            select(Artefacto)
            .where(Artefacto.mac == "AA:BB:CC:DD:EE:01")
            .options(selectinload(Artefacto.horario))
        )
        result = await db.execute(stmt)
        dev = result.scalar_one()

        assert dev.estado_deseado is False, f"Device should be turned off, got {dev.estado_deseado}"
        assert dev.auto_kill_at is None, "auto_kill_at should be cleared"
        assert dev.horario is not None, "Schedule should still exist"
        assert dev.horario.automatizacion_activa is False, "Schedule automation should be disabled!"
        print("  [OK] AI Auto-Kill correctly disabled schedule automation!")

    # Verify push notification mentioned automation
    assert rec_engine.enviar_push_a_duenos.called, "Push notification should have been sent"
    last_call = rec_engine.enviar_push_a_duenos.call_args_list[-1]
    # enviar_push_a_duenos(db, mac, titulo, cuerpo) — body is at index 3
    body = last_call[0][3] if len(last_call[0]) > 3 else ""
    assert "automatización" in body, f"Push should mention automation, got: {body}"
    print(f"  [OK] Push notification: {body[:100]}...")

    await engine.dispose()
    print("  [PASS] Test 1 passed!")


async def test_p3_auto_kill_disables_schedule():
    """Test 2: P3 immediate auto-kill disables active schedule automation."""
    print("\n--- TEST 2: P3 Auto-Kill Disables Schedule ---")
    engine, TestSession = await setup_database()

    import app.recommendation_engine as rec_engine
    rec_engine.AsyncSessionLocal = TestSession
    rec_engine._publish_mqtt = AsyncMock()
    rec_engine._broadcast_event = AsyncMock()
    rec_engine.enviar_push_a_duenos = AsyncMock()

    async with TestSession() as db:
        device, user = await seed_device_with_schedule(db, "AA:BB:CC:DD:EE:02", "P3 Kill Test", prioridad="P3")

    # Insert RISKY telemetry — dense readings in the last 2 min for AI_CONTROL_RISKY_THRESHOLD_MIN
    async with TestSession() as db:
        stmt = select(Artefacto).where(Artefacto.mac == "AA:BB:CC:DD:EE:02")
        result = await db.execute(stmt)
        dev = result.scalar_one()

        # 10 readings over the last 2 minutes (every 12 seconds) — well above min_count=2
        for i in range(10):
            t = Telemetria(
                id_artefacto=dev.id,
                voltaje=120.0,
                corriente=5.0,
                potencia=60.0,
                tiempo_operacion_s=i * 12,
                ai_status=1,
                timestamp=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=120 - i * 12),
            )
            db.add(t)
        await db.commit()

    await rec_engine.scan_all_devices()

    async with TestSession() as db:
        stmt = (
            select(Artefacto)
            .where(Artefacto.mac == "AA:BB:CC:DD:EE:02")
            .options(selectinload(Artefacto.horario))
        )
        result = await db.execute(stmt)
        dev = result.scalar_one()

        assert dev.estado_deseado is False, f"P3 device should be turned off, got {dev.estado_deseado}"
        assert dev.horario.automatizacion_activa is False, "P3 schedule automation should be disabled!"
        print("  [OK] P3 Auto-Kill correctly disabled schedule automation!")

    await engine.dispose()
    print("  [PASS] Test 2 passed!")


async def test_bms_emergency_disables_schedule():
    """Test 3: BMS emergency shutdown disables active schedule automation."""
    print("\n--- TEST 3: BMS Emergency Shutdown Disables Schedule ---")
    engine, TestSession = await setup_database()

    from app.crud import emergencia_bms_shutdown

    async with TestSession() as db:
        device, user = await seed_device_with_schedule(db, "AA:BB:CC:DD:EE:03", "BMS Test")

    async with TestSession() as db:
        result = await emergencia_bms_shutdown(db, "AA:BB:CC:DD:EE:03", "Battery critical", 2)

        assert result is not None, "BMS shutdown should return a result"
        dispositivo, alerta_creada, automation_disabled = result

        assert dispositivo.estado_deseado is False, "Device should be turned off"
        assert dispositivo.estado_reportado is False, "Reported state should be off"
        assert alerta_creada is True, "BMS alert should be created"
        assert automation_disabled is True, "Automation should be reported as disabled"
        print("  [OK] BMS shutdown returned automation_disabled=True!")

    # Verify schedule in DB
    async with TestSession() as db:
        stmt = (
            select(Artefacto)
            .where(Artefacto.mac == "AA:BB:CC:DD:EE:03")
            .options(selectinload(Artefacto.horario))
        )
        result = await db.execute(stmt)
        dev = result.scalar_one()

        assert dev.horario.automatizacion_activa is False, "Schedule automation should be disabled!"
        print("  [OK] BMS shutdown correctly disabled schedule automation in DB!")

    # Verify event log
    async with TestSession() as db:
        stmt = (
            select(EventoUsuario)
            .where(EventoUsuario.accion == "safety_override")
            .order_by(EventoUsuario.id.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        evento = result.scalar_one_or_none()

        assert evento is not None, "Safety override event should exist"
        assert "automatización desactivada" in evento.razon_disparo, \
            f"Event should mention automation disabled, got: {evento.razon_disparo}"
        print(f"  [OK] Event logged: {evento.razon_disparo}")

    await engine.dispose()
    print("  [PASS] Test 3 passed!")


async def test_threshold_violation_disables_schedule():
    """Test 4: Critical threshold violation disables active schedule automation."""
    print("\n--- TEST 4: Critical Threshold Violation Disables Schedule ---")
    engine, TestSession = await setup_database()

    from app.mqtt_listener import _verificar_alertas
    from app.schemas import TelemetriaCreate

    async with TestSession() as db:
        device, user = await seed_device_with_schedule(db, "AA:BB:CC:DD:EE:04", "Threshold Test")

    # Directly call _verificar_alertas with over-voltage data
    with patch("app.mqtt_listener.mqtt_publish.single") as mock_mqtt, \
         patch("app.crud.enviar_push_a_duenos", new_callable=AsyncMock), \
         patch("app.mqtt_listener.enviar_push_a_duenos", new_callable=AsyncMock) as mock_push:

        async with TestSession() as db:
            from app.crud import obtener_dispositivo_por_telemetria
            artefacto = await obtener_dispositivo_por_telemetria(db, "AA:BB:CC:DD:EE:04")
            assert artefacto is not None, "Device should exist"

            # Create a mock telemetria input with over-voltage (limit is 130V, sending 140V)
            telemetria_in = TelemetriaCreate(
                mac_dispositivo="AA:BB:CC:DD:EE:04",
                voltaje=140.0,
                corriente=5.0,
                potencia=60.0,
                tiempo_operacion_s=120,
                ai_status=0,
            )

            await _verificar_alertas(db, artefacto, telemetria_in)

        # Verify MQTT shutdown was published
        if mock_mqtt.called:
            call_args = mock_mqtt.call_args
            print(f"  [OK] MQTT shutdown published to: {call_args[0][0]}")
        else:
            print("  [WARN] MQTT shutdown not published (may be deduplicated)")

    # Verify state in DB
    async with TestSession() as db:
        stmt = (
            select(Artefacto)
            .where(Artefacto.mac == "AA:BB:CC:DD:EE:04")
            .options(selectinload(Artefacto.horario))
        )
        result = await db.execute(stmt)
        dev = result.scalar_one()

        assert dev.estado_deseado is False, f"Device should be turned off, got {dev.estado_deseado}"
        assert dev.horario.automatizacion_activa is False, "Schedule automation should be disabled!"
        print("  [OK] Threshold violation correctly disabled schedule automation!")

    await engine.dispose()
    print("  [PASS] Test 4 passed!")


async def test_no_schedule_does_not_crash():
    """Test 5: Devices without schedules should not crash during security overrides."""
    print("\n--- TEST 5: No Schedule Does Not Crash ---")
    engine, TestSession = await setup_database()

    from app.crud import emergencia_bms_shutdown

    async with TestSession() as db:
        device = Artefacto(
            mac="AA:BB:CC:DD:EE:05",
            nombre_personalizado="No Schedule Test",
            nivel_prioridad="P2",
            estado_deseado=True,
            estado_reportado=True,
            is_online=True,
        )
        db.add(device)
        await db.commit()

    async with TestSession() as db:
        result = await emergencia_bms_shutdown(db, "AA:BB:CC:DD:EE:05", "Battery critical", 2)

        assert result is not None, "BMS shutdown should work without a schedule"
        dispositivo, alerta_creada, automation_disabled = result

        assert dispositivo.estado_deseado is False, "Device should be turned off"
        assert alerta_creada is True, "Alert should be created"
        assert automation_disabled is False, "No schedule, so automation_disabled should be False"
        print("  [OK] BMS shutdown worked correctly without a schedule (automation_disabled=False)!")

    await engine.dispose()
    print("  [PASS] Test 5 passed!")


async def main():
    print("=" * 60)
    print("Security Overrides Test Suite")
    print("=" * 60)

    await test_ai_auto_kill_disables_schedule()
    await test_p3_auto_kill_disables_schedule()
    await test_bms_emergency_disables_schedule()
    await test_threshold_violation_disables_schedule()
    await test_no_schedule_does_not_crash()

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
