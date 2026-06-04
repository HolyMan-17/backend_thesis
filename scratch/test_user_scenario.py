import asyncio
import os
import sys
from datetime import datetime, timezone, time, timedelta

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

# Setup system path to import app package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import Base
from app.models import Usuario, Artefacto, ArtefactoHorario, EventoUsuario, NotificacionUsuario
from app.exceptions import AppException
import app.schedule_engine
import app.main

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

async def run_scenario():
    print("Setting up database...")
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    
    from sqlalchemy import Integer
    EventoUsuario.__table__.c.id.type = Integer()
    NotificacionUsuario.__table__.c.id.type = Integer()

    tables_to_create = [
        Usuario.__table__,
        Artefacto.__table__,
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
    
    app.schedule_engine.AsyncSessionLocal = AsyncSessionTest
    
    import paho.mqtt.publish as publish
    publish.single = lambda *args, **kwargs: print(f"  [Mock MQTT] Published to {args[0]}: {args[1]}")

    async with AsyncSessionTest() as db:
        print("Seeding mock user and device...")
        user = Usuario(
            auth0_id="auth0|testuser1",
            email="test1@example.com",
            nombre="User Test 1",
            expo_push_token=None,
        )
        device = Artefacto(
            mac="11:22:33:44:55:66",
            nombre_personalizado="Test Device",
            nivel_prioridad="P2",
            estado_deseado=False,
            estado_reportado=False,
            is_online=True,
        )
        db.add_all([user, device])
        await db.commit()
        await db.refresh(user)
        await db.refresh(device)
        user_id = user.id

    # 1. User sets a schedule starting at 4:32 PM (16:32) to 6:00 PM (18:00)
    # We simulate this at 4:31 PM (16:31)
    print("\n--- Step 1: User sets schedule at 4:31 PM ---")
    current_time_4_31 = datetime.now().replace(hour=16, minute=31, second=0, microsecond=0)
    
    # Mock datetime.now in schedule_engine and main
    import zoneinfo
    tz = zoneinfo.ZoneInfo("America/Caracas")
    
    # Mocking datetime inside schedule_engine and main
    original_datetime = datetime
    class MockDatetime:
        @classmethod
        def now(cls, tz_arg=None):
            # Return fixed 4:31 PM time in Caracas timezone
            if tz_arg:
                return current_time_4_31.astimezone(tz_arg)
            return current_time_4_31
    
    # Apply mocks
    app.schedule_engine.datetime = MockDatetime
    app.main.datetime = MockDatetime
    
    async with AsyncSessionTest() as db:
        # Simulate PUT /api/dispositivos/{mac}/horario
        from app.schemas import HorarioUpdate
        horario_in = HorarioUpdate(
            dias_operacion=[1, 2, 3, 4, 5, 6, 7],
            hora_encendido=time(16, 32),
            hora_apagado=time(18, 0),
            automatizacion_activa=True,
        )
        
        # We manually call main.actualizar_horario logic
        # but in a simplified form with our test db session
        datos = horario_in.model_dump(exclude_unset=True)
        horario = await app.main.actualizar_horario_dispositivo(db, device.mac, datos)
        
        # Check immediate evaluation at 4:31 PM
        start_min = horario.hora_encendido.hour * 60 + horario.hora_encendido.minute
        end_min = horario.hora_apagado.hour * 60 + horario.hora_apagado.minute
        current_minutes = 16 * 60 + 31
        
        should_be_on = app.schedule_engine.check_should_be_on(
            current_day=current_time_4_31.isoweekday(),
            current_min=current_minutes,
            dias_operacion=horario.dias_operacion,
            start_min=start_min,
            end_min=end_min
        )
        print(f"  At 4:31 PM, should_be_on: {should_be_on}")
        assert should_be_on is False
        
        device_is_on = device.estado_deseado
        print(f"  Device estado_deseado is: {device_is_on}")
        assert device_is_on is False
        
        if should_be_on != device_is_on:
            await app.main.comando_estado_con_lease(db, device.mac, encendido=should_be_on)
        else:
            print("  [OK] No immediate transition needed at 4:31 PM.")

    # 2. Clock ticks to 4:32 PM (16:32). Schedule engine evaluates.
    print("\n--- Step 2: Clock ticks to 4:32 PM and Schedule engine runs ---")
    current_time_4_32 = datetime.now().replace(hour=16, minute=32, second=0, microsecond=0)
    class MockDatetime432:
        @classmethod
        def now(cls, tz_arg=None):
            if tz_arg:
                return current_time_4_32.astimezone(tz_arg)
            return current_time_4_32
            
    app.schedule_engine.datetime = MockDatetime432
    app.main.datetime = MockDatetime432
    
    # Initialize schedule cache (like startup)
    app.schedule_engine._last_schedule_states.clear()
    app.schedule_engine._is_first_evaluation = True
    
    # Run evaluation
    await app.schedule_engine._evaluate_schedules()
    
    # Check device state in DB
    async with AsyncSessionTest() as db:
        dev = await db.get(Artefacto, device.id)
        print(f"  Device estado_deseado in DB: {dev.estado_deseado}")
        print(f"  Device override_activo in DB: {dev.override_activo}")
        print(f"  Device vencimiento_lease in DB: {dev.vencimiento_lease}")
        
    # 3. User tries to turn it ON manually at 4:33 PM
    print("\n--- Step 3: User tries to turn ON manually at 4:33 PM ---")
    current_time_4_33 = datetime.now().replace(hour=16, minute=33, second=0, microsecond=0)
    class MockDatetime433:
        @classmethod
        def now(cls, tz_arg=None):
            if tz_arg:
                return current_time_4_33.astimezone(tz_arg)
            return current_time_4_33
            
    app.schedule_engine.datetime = MockDatetime433
    app.main.datetime = MockDatetime433
    
    async with AsyncSessionTest() as db:
        # Load device with horario selectinload
        from sqlalchemy.orm import selectinload
        stmt = select(Artefacto).where(Artefacto.id == device.id).options(selectinload(Artefacto.horario))
        res = await db.execute(stmt)
        dev = res.scalar_one()
        
        # Test command turning ON: encendido=True, override_automation=False
        from app.schemas import ComandoEstado
        comando = ComandoEstado(encendido=True, override_automation=False)
        
        # Replicate main.comando_estado check
        in_schedule = False
        current_day = current_time_4_33.isoweekday()
        current_minutes = 16 * 60 + 33
        
        if dev.horario and dev.horario.automatizacion_activa:
            if dev.horario.dias_operacion and current_day in dev.horario.dias_operacion:
                if dev.horario.hora_encendido and dev.horario.hora_apagado:
                    start_min = dev.horario.hora_encendido.hour * 60 + dev.horario.hora_encendido.minute
                    end_min = dev.horario.hora_apagado.hour * 60 + dev.horario.hora_apagado.minute
                    if start_min <= current_minutes < end_min:
                        in_schedule = True
                        
        print(f"  In schedule: {in_schedule}")
        print(f"  Device estado_reportado: {dev.estado_reportado}")
        print(f"  Comando encendido: {comando.encendido}")
        
        # Raise check
        try:
            if in_schedule and dev.estado_reportado and not comando.encendido:
                if not comando.override_automation:
                    raise AppException(error="automation_active", message="El dispositivo está operando dentro del horario establecido. Se requiere override_automation=true para proceder.", status_code=409)
            print("  [PASS] Turning ON manually succeeded without lock error!")
        except AppException as e:
            print(f"  [FAIL] Turning ON manually raised exception: {e.message}")
            
    # 4. What if the database thinks the device is ON (estado_reportado=True) but user tries to turn it ON?
    print("\n--- Step 4: If database thinks it is ON, user tries to turn it ON manually ---")
    async with AsyncSessionTest() as db:
        stmt = select(Artefacto).where(Artefacto.id == device.id).options(selectinload(Artefacto.horario))
        res = await db.execute(stmt)
        dev = res.scalar_one()
        dev.estado_reportado = True
        await db.commit()
        
        # Try turning ON manually
        in_schedule = True
        comando = ComandoEstado(encendido=True, override_automation=False)
        
        try:
            if in_schedule and dev.estado_reportado and not comando.encendido:
                if not comando.override_automation:
                    raise AppException(error="automation_active", message="El dispositivo está operando dentro del horario establecido. Se requiere override_automation=true para proceder.", status_code=409)
            print("  [PASS] Turning ON when reported ON succeeded without lock error!")
        except AppException as e:
            print(f"  [FAIL] Turning ON raised exception: {e.message}")

    # 5. What if database thinks it is ON, but user tries to turn it OFF?
    print("\n--- Step 5: If database thinks it is ON, user tries to turn it OFF manually ---")
    async with AsyncSessionTest() as db:
        stmt = select(Artefacto).where(Artefacto.id == device.id).options(selectinload(Artefacto.horario))
        res = await db.execute(stmt)
        dev = res.scalar_one()
        comando = ComandoEstado(encendido=False, override_automation=False)
        try:
            if in_schedule and dev.estado_reportado and not comando.encendido:
                if not comando.override_automation:
                    raise AppException(error="automation_active", message="El dispositivo está operando dentro del horario establecido. Se requiere override_automation=true para proceder.", status_code=409)
            print("  [PASS] Turning OFF succeeded!")
        except AppException as e:
            print(f"  [EXPECTED LOCK] Turning OFF raised exception: {e.message}")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(run_scenario())
