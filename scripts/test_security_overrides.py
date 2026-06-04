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
        assert dev.estado_reportado is False, f"Reported state should be off, got {dev.estado_reportado}"
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
        assert dev.estado_reportado is False, f"P3 reported state should be off, got {dev.estado_reportado}"
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
        assert dev.estado_reportado is False, f"Reported state should be off, got {dev.estado_reportado}"
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


async def test_manual_override_lock_behavior():
    """Test 6: Manual toggle automation lock and override behaviors."""
    print("\n--- TEST 6: Manual Toggle Automation Lock & Bypass ---")
    engine, TestSession = await setup_database()

    from app.main import comando_estado
    from app.schemas import ComandoEstado
    from app.exceptions import AppException

    async with TestSession() as db:
        device, user = await seed_device_with_schedule(db, "AA:BB:CC:DD:EE:06", "Manual Lock Test")
        
        # Ensure it is currently reported as ON and schedule is active
        device.estado_reportado = True
        device.estado_deseado = True
        await db.commit()

        # Patch the MQTT publish to avoid network errors
        with patch("app.main.publish.single") as mock_publish:
            # Scenario A: Try to turn device OFF without override_automation=True.
            # Since in_schedule is True and estado_reportado is True and comando.encendido is False, it should block.
            try:
                await comando_estado(
                    mac="AA:BB:CC:DD:EE:06",
                    comando=ComandoEstado(encendido=False, override_automation=False),
                    db=db,
                    user=user,
                )
                assert False, "Should have failed with AppException (automation_active)"
            except AppException as exc:
                assert exc.error == "automation_active", f"Expected automation_active error, got: {exc.error}"
                print("  [OK] Successfully blocked turning OFF the device during active schedule without override.")

            # Scenario B: Try to turn device OFF with override_automation=True.
            # This should succeed and disable automation.
            await comando_estado(
                mac="AA:BB:CC:DD:EE:06",
                comando=ComandoEstado(encendido=False, override_automation=True),
                db=db,
                user=user,
            )
            # Fetch from DB and check state
            stmt = select(ArtefactoHorario).where(ArtefactoHorario.id_artefacto == device.id)
            res = await db.execute(stmt)
            schedule = res.scalar_one()
            assert schedule.automatizacion_activa is False, "Schedule should be disabled after manual OFF with override"
            print("  [OK] Successfully turned OFF device and disabled schedule using override.")

            # Re-activate schedule and set reported state to False (physically OFF, e.g. after safety shutdown)
            schedule.automatizacion_activa = True
            device.estado_reportado = False
            await db.commit()

            # Scenario C: Try to turn device ON without override_automation=True.
            # Since the device is physically OFF (estado_reportado is False), and/or we are turning it ON,
            # this should succeed and NOT trigger the lock.
            await comando_estado(
                mac="AA:BB:CC:DD:EE:06",
                comando=ComandoEstado(encendido=True, override_automation=False),
                db=db,
                user=user,
            )
            
            # Fetch from DB to verify it turned on and schedule is still active
            stmt_dev = select(Artefacto).where(Artefacto.mac == "AA:BB:CC:DD:EE:06")
            res_dev = await db.execute(stmt_dev)
            dev = res_dev.scalar_one()
            assert dev.estado_deseado is True, "Device should be commanded to turn ON"
            
            # Check schedule is still active
            stmt_sched = select(ArtefactoHorario).where(ArtefactoHorario.id_artefacto == device.id)
            res_sched = await db.execute(stmt_sched)
            sched = res_sched.scalar_one()
            assert sched.automatizacion_activa is True, "Schedule should remain active"
            print("  [OK] Successfully turned ON device during active schedule window without lock trigger.")

    await engine.dispose()
    print("  [PASS] Test 6 passed!")


async def test_ai_auto_kill_respects_user_lease():
    """Test 7: AI Auto-Kill respects active user override leases."""
    print("\n--- TEST 7: AI Auto-Kill Respects User Override Leases ---")
    engine, TestSession = await setup_database()

    import app.recommendation_engine as rec_engine
    rec_engine.AsyncSessionLocal = TestSession
    rec_engine._publish_mqtt = AsyncMock()
    rec_engine._broadcast_event = AsyncMock()
    rec_engine.enviar_push_a_duenos = AsyncMock()

    async with TestSession() as db:
        # A: Regular device with lease
        device, user = await seed_device_with_schedule(db, "AA:BB:CC:DD:EE:07", "AI Lease Test")
        # Set auto_kill_at in the past
        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
        device.auto_kill_at = past
        # Set active user override lease
        device.override_activo = True
        device.vencimiento_lease = datetime.now(timezone.utc) + timedelta(minutes=4)
        await db.commit()

        # B: P3 device with lease
        device_p3, user_p3 = await seed_device_with_schedule(db, "AA:BB:CC:DD:EE:08", "P3 Lease Test", prioridad="P3")
        device_p3.override_activo = True
        device_p3.vencimiento_lease = datetime.now(timezone.utc) + timedelta(minutes=4)
        await db.commit()

    # Seed RISKY telemetry for both
    async with TestSession() as db:
        for mac in ["AA:BB:CC:DD:EE:07", "AA:BB:CC:DD:EE:08"]:
            stmt = select(Artefacto).where(Artefacto.mac == mac)
            res = await db.execute(stmt)
            dev = res.scalar_one()
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

    # Run recommendation scan
    await rec_engine.scan_all_devices()

    # Verify both auto-kills were skipped and device states remain True/ON
    async with TestSession() as db:
        for mac in ["AA:BB:CC:DD:EE:07", "AA:BB:CC:DD:EE:08"]:
            stmt = select(Artefacto).where(Artefacto.mac == mac)
            res = await db.execute(stmt)
            dev = res.scalar_one()
            assert dev.estado_deseado is True, f"Device {mac} should remain ON due to user lease!"
            assert dev.override_activo is True, f"Device {mac} lease should remain active"
            print(f"  [OK] AI Auto-Kill successfully skipped for {mac} due to active lease.")

    await engine.dispose()
    print("  [PASS] Test 7 passed!")


async def test_split_mqtt_clients_handling():
    """Test 8: Split MQTT clients handle and route messages correctly."""
    print("\n--- TEST 8: Split MQTT Clients Routing Verification ---")
    
    from app.mqtt_listener import on_message_db, on_message_ws
    
    # Mock message class
    class MockMsg:
        def __init__(self, topic, payload):
            self.topic = topic
            self.payload = payload.encode("utf-8") if isinstance(payload, str) else payload

    # Mock the global main loop and processing helpers
    with patch("app.mqtt_listener._main_loop") as mock_loop, \
         patch("app.mqtt_listener.procesar_payload", new_callable=AsyncMock) as mock_proc_payload, \
         patch("app.ws_manager.ws_manager.broadcast_telemetry", new_callable=AsyncMock) as mock_broadcast_telemetry, \
         patch("app.ws_manager.ws_manager.broadcast_event", new_callable=AsyncMock) as mock_broadcast_event, \
         patch("asyncio.run_coroutine_threadsafe") as mock_run_coroutine_threadsafe:
        
        # Make loop running check succeed
        mock_loop.is_running.return_value = True
        
        # Scenario A: Telemetry message to DB client (shared)
        msg_db = MockMsg("smartups/dispositivos/AA:BB:CC:DD:EE:09/telemetria", '{"voltaje": 120, "corriente": 1, "potencia": 120, "mac_dispositivo": "AA:BB:CC:DD:EE:09", "tiempo_operacion_s": 100}')
        on_message_db(None, None, msg_db)
        
        # Verify it scheduled procesar_payload
        mock_proc_payload.assert_called_once_with("smartups/dispositivos/AA:BB:CC:DD:EE:09/telemetria", '{"voltaje": 120, "corriente": 1, "potencia": 120, "mac_dispositivo": "AA:BB:CC:DD:EE:09", "tiempo_operacion_s": 100}')
        assert mock_run_coroutine_threadsafe.called, "Should run coroutine threadsafe"
        assert mock_run_coroutine_threadsafe.call_args[0][1] == mock_loop
        print("  [OK] DB client message correctly routed to procesar_payload.")

        # Reset mocks
        mock_proc_payload.reset_mock()
        mock_run_coroutine_threadsafe.reset_mock()

        # Scenario B: Telemetry message to WS client (non-shared)
        msg_ws = MockMsg("smartups/dispositivos/AA:BB:CC:DD:EE:09/telemetria", '{"voltaje": 120, "corriente": 1, "potencia": 120, "mac_dispositivo": "AA:BB:CC:DD:EE:09", "tiempo_operacion_s": 100}')
        on_message_ws(None, None, msg_ws)
        
        # Verify it did not call procesar_payload, but broadcasted to WebSockets
        assert not mock_proc_payload.called, "WS client telemetry should not call procesar_payload"
        mock_broadcast_telemetry.assert_called_once_with(
            "AA:BB:CC:DD:EE:09",
            {"voltaje": 120, "corriente": 1, "potencia": 120, "mac_dispositivo": "AA:BB:CC:DD:EE:09", "tiempo_operacion_s": 100}
        )
        assert mock_run_coroutine_threadsafe.called, "Should run coroutine threadsafe"
        assert mock_run_coroutine_threadsafe.call_args[0][1] == mock_loop
        print("  [OK] WS client telemetry message correctly routed directly to WebSocket broadcast (no DB write).")

        # Reset mocks
        mock_broadcast_telemetry.reset_mock()
        mock_run_coroutine_threadsafe.reset_mock()

        # Scenario C: Connection message to WS client
        msg_conn = MockMsg("smartups/dispositivos/AA:BB:CC:DD:EE:09/conexion", '{"is_online": true}')
        on_message_ws(None, None, msg_conn)
        
        # Verify it broadcasted connection event
        mock_broadcast_event.assert_called_once_with("AA:BB:CC:DD:EE:09", "conexion", {"is_online": True})
        assert mock_run_coroutine_threadsafe.called, "Should run coroutine threadsafe"
        assert mock_run_coroutine_threadsafe.call_args[0][1] == mock_loop
        print("  [OK] WS client connection message correctly broadcasted connection event.")

        # Reset mocks
        mock_broadcast_event.reset_mock()
        mock_run_coroutine_threadsafe.reset_mock()

        # Scenario D: Broadcast event message to WS client (e.g. from recommendation engine auto_kill_warning)
        msg_bc = MockMsg("smartups/dispositivos/AA:BB:CC:DD:EE:09/broadcast/auto_kill_warning", '{"message": "Auto-kill warning!"}')
        on_message_ws(None, None, msg_bc)
        
        # Verify it broadcasted the auto_kill_warning event
        mock_broadcast_event.assert_called_once_with("AA:BB:CC:DD:EE:09", "auto_kill_warning", {"message": "Auto-kill warning!"})
        assert mock_run_coroutine_threadsafe.called, "Should run coroutine threadsafe"
        assert mock_run_coroutine_threadsafe.call_args[0][1] == mock_loop
        print("  [OK] WS client broadcast event correctly routed to WS manager with parsed event type.")

    print("  [PASS] Test 8 passed!")

    print("  [PASS] Test 8 passed!")


async def main():
    print("=" * 60)
    print("Security Overrides Test Suite")
    print("=" * 60)

    await test_ai_auto_kill_disables_schedule()
    await test_p3_auto_kill_disables_schedule()
    await test_bms_emergency_disables_schedule()
    await test_threshold_violation_disables_schedule()
    await test_no_schedule_does_not_crash()
    await test_manual_override_lock_behavior()
    await test_ai_auto_kill_respects_user_lease()
    await test_split_mqtt_clients_handling()

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
