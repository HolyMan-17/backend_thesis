import asyncio
import os
import sys
from datetime import datetime, timezone, time, timedelta

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Setup system path to import app package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import Base
from app.models import Usuario, Artefacto, ArtefactoHorario, EventoUsuario, PermisoUsuarioArtefacto, NotificacionUsuario
import app.schedule_engine

# Use SQLite in-memory database for local testing
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

async def test_schedule_engine_logic():
    print("Setting up SQLite in-memory database engine for schedule tests...")
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    
    from sqlalchemy import Integer
    EventoUsuario.__table__.c.id.type = Integer()
    NotificacionUsuario.__table__.c.id.type = Integer()

    tables_to_create = [
        Usuario.__table__,
        Artefacto.__table__,
        PermisoUsuarioArtefacto.__table__,
        ArtefactoHorario.__table__,
        EventoUsuario.__table__,
        NotificacionUsuario.__table__,
    ]
    async with engine.begin() as conn:
        for table in tables_to_create:
            await conn.run_sync(table.create)
    
    AsyncSessionTest = sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    # Mock AsyncSessionLocal inside schedule_engine
    app.schedule_engine.AsyncSessionLocal = AsyncSessionTest
    
    # Mock MQTT publishing to avoid errors during test execution
    import paho.mqtt.publish as publish
    publish.single = lambda *args, **kwargs: print(f"  [Mock MQTT] Published message to {args[0]}")

    async with AsyncSessionTest() as db:
        print("Inserting mock device and schedule...")
        # Device starts online, estado_deseado is True (ON)
        device = Artefacto(
            mac="11:22:33:44:55:66",
            nombre_personalizado="Mock Device",
            nivel_prioridad="P2",
            estado_deseado=True,
            estado_reportado=True,
            is_online=True,
        )
        db.add(device)
        await db.commit()
        await db.refresh(device)
        
        # Schedule: active, runs every day (1-7),
        # start time: 1 hour after now, end time: 2 hours after now.
        # This ensures should_be_on evaluates to False.
        now_local = datetime.now()
        start_time = (now_local + timedelta(hours=1)).time()
        end_time = (now_local + timedelta(hours=2)).time()
        schedule = ArtefactoHorario(
            id_artefacto=device.id,
            dias_operacion=[1, 2, 3, 4, 5, 6, 7],
            hora_encendido=start_time,
            hora_apagado=end_time,
            automatizacion_activa=True,
        )
        db.add(schedule)
        await db.commit()
        await db.refresh(schedule)
        
        device_id = device.id

    print("\n--- 1. Testing Startup Seeding (Manual Override Protection) ---")
    # Reset schedule engine state
    app.schedule_engine._last_schedule_states.clear()
    app.schedule_engine._is_first_evaluation = True
    
    # Verify that before running evaluation, dispositivo is ON (estado_deseado=True)
    async with AsyncSessionTest() as db:
        device_db = await db.get(Artefacto, device_id)
        assert device_db.estado_deseado is True

    # Run evaluation. It should seed the cache but NOT transition the device to False
    print("Running _evaluate_schedules for startup...")
    await app.schedule_engine._evaluate_schedules()
    
    # Check that in-memory state is seeded
    cached_state = app.schedule_engine._last_schedule_states.get(device_id)
    assert cached_state is not None
    assert cached_state["should_be_on"] is False  # Outside 8:00 - 11:34 AM window
    
    # Check that dispositivo remained ON (no transition executed)
    async with AsyncSessionTest() as db:
        device_db = await db.get(Artefacto, device_id)
        assert device_db.estado_deseado is True
        print("  [OK] Startup seeding did not override the user's manual state!")
        
    # Check that _is_first_evaluation was set to False after the first run
    assert app.schedule_engine._is_first_evaluation is False

    print("\n--- 2. Testing Manual Override Retention in subsequent runs ---")
    # Execute a second evaluation. Since should_be_on=False and prev_should_be_on=False,
    # it should NOT touch the device (which is still ON)
    print("Running _evaluate_schedules again...")
    await app.schedule_engine._evaluate_schedules()
    
    async with AsyncSessionTest() as db:
        device_db = await db.get(Artefacto, device_id)
        assert device_db.estado_deseado is True
        print("  [OK] Subsequent evaluations did not override manual control!")

    print("\n--- 3. Testing Offline Device Handling ---")
    # Turn device offline in DB
    async with AsyncSessionTest() as db:
        device_db = await db.get(Artefacto, device_id)
        device_db.is_online = False
        await db.commit()
        
    # Evaluate schedules.
    # Since is_online filter is removed, the schedule is still processed.
    # The cache should still contain the state and NOT be popped by housekeeping.
    print("Evaluating with offline device...")
    await app.schedule_engine._evaluate_schedules()
    
    cached_state = app.schedule_engine._last_schedule_states.get(device_id)
    assert cached_state is not None, "Cache was popped while device was offline!"
    print("  [OK] Device schedule was not popped from cache while offline!")

    print("\n--- 4. Testing Newly Activated Schedule Sync ---")
    # Create a new device that is currently OFF, but has a schedule that should be ON.
    # We turn on automation for it. Since _is_first_evaluation is False, it should immediately sync it to ON.
    async with AsyncSessionTest() as db:
        device2 = Artefacto(
            mac="AA:BB:CC:DD:EE:FF",
            nombre_personalizado="Mock Device 2",
            nivel_prioridad="P2",
            estado_deseado=False,  # OFF
            estado_reportado=False,
            is_online=True,
        )
        db.add(device2)
        await db.commit()
        await db.refresh(device2)
        
        # Set schedule to encompass current time: 1 hour before now to 1 hour after now.
        # This ensures should_be_on evaluates to True.
        now_local = datetime.now()
        start_time = (now_local - timedelta(hours=1)).time()
        end_time = (now_local + timedelta(hours=1)).time()
        schedule2 = ArtefactoHorario(
            id_artefacto=device2.id,
            dias_operacion=[1, 2, 3, 4, 5, 6, 7],
            hora_encendido=start_time,
            hora_apagado=end_time,
            automatizacion_activa=True,
        )
        db.add(schedule2)
        await db.commit()
        await db.refresh(schedule2)
        
        device2_id = device2.id

    print("Evaluating new active schedule...")
    await app.schedule_engine._evaluate_schedules()
    
    # Verify that device 2 was turned ON immediately
    async with AsyncSessionTest() as db:
        device2_db = await db.get(Artefacto, device2_id)
        assert device2_db.estado_deseado is True
        print("  [OK] Newly enabled schedule forced sync immediately during runtime!")

    print("\n--- 5. Testing Active User Lease Override Skip ---")
    # For Device 2: currently should_be_on is True, cache has should_be_on = True.
    # We set its database status to have an active lease: override_activo = True, vencimiento_lease = now + 5 mins.
    # We also change its schedule start/end time so that should_be_on evaluates to False (e.g. 1 hour after now to 2 hours after now).
    # Then we run evaluation. The transition from True to False should be SKIPPED due to active lease,
    # but the cache should still update to False.
    async with AsyncSessionTest() as db:
        # Update schedule 2 to be inactive at current time (should_be_on = False)
        sched2 = await db.get(ArtefactoHorario, device2_id)
        now_local = datetime.now()
        sched2.hora_encendido = (now_local + timedelta(hours=1)).time()
        sched2.hora_apagado = (now_local + timedelta(hours=2)).time()
        
        # Enable active user override lease on Device 2
        dev2 = await db.get(Artefacto, device2_id)
        dev2.estado_deseado = True
        dev2.override_activo = True
        dev2.vencimiento_lease = datetime.now(timezone.utc) + timedelta(minutes=5)
        await db.commit()
        
    print("Evaluating transition with active user lease...")
    await app.schedule_engine._evaluate_schedules()
    
    # Verify that Device 2 is STILL ON (transition to OFF was skipped)
    async with AsyncSessionTest() as db:
        device2_db = await db.get(Artefacto, device2_id)
        assert device2_db.estado_deseado is True
        print("  [OK] Schedule transition was successfully skipped due to active user lease!")
        
    # Verify that cache updated its should_be_on to False
    cached_state = app.schedule_engine._last_schedule_states.get(device2_id)
    assert cached_state["should_be_on"] is False, "Cache should update to represent the crossed boundary"
    print("  [OK] Cache updated state correctly to prevent transition loop!")

    print("\nAll schedule engine checks passed successfully!")
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(test_schedule_engine_logic())
