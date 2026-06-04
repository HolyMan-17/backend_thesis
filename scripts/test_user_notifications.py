import asyncio
import os
import sys
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Setup system path to import app package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import Base
from app.models import Usuario, Artefacto, PermisoUsuarioArtefacto, NotificacionUsuario
from app.crud import (
    enviar_push_a_duenos,
    obtener_notificaciones_usuario,
    actualizar_notificacion_usuario,
    eliminar_todas_notificaciones_usuario,
    comando_estado_con_lease,
)

# Use SQLite in-memory database for local testing
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

async def test_notifications():
    print("Initializing SQLite in-memory database engine for testing...")
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    
    # Create only the tables needed for this test to bypass SQLite composite PK limitations
    from app.models import EventoUsuario
    from sqlalchemy import Integer
    # SQLite requires INTEGER (not BIGINT) to autoincrement primary keys
    EventoUsuario.__table__.c.id.type = Integer()
    NotificacionUsuario.__table__.c.id.type = Integer()

    tables_to_create = [
        Usuario.__table__,
        Artefacto.__table__,
        PermisoUsuarioArtefacto.__table__,
        NotificacionUsuario.__table__,
        EventoUsuario.__table__,
    ]
    async with engine.begin() as conn:
        for table in tables_to_create:
            await conn.run_sync(table.create)
    
    AsyncSessionTest = sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    async with AsyncSessionTest() as db:
        print("Inserting mock users, devices, and access permissions...")
        user1 = Usuario(
            auth0_id="auth0|testuser1",
            email="test1@example.com",
            nombre="User Test 1",
            expo_push_token="ExponentPushToken[mock_token_1]",
        )
        user2 = Usuario(
            auth0_id="auth0|testuser2",
            email="test2@example.com",
            nombre="User Test 2",
            expo_push_token=None,  # No token registered
        )
        device = Artefacto(
            mac="11:22:33:44:55:66",
            nivel_prioridad="P2",
            estado_deseado=False,
            estado_reportado=False,
            is_online=True,
        )
        db.add_all([user1, user2, device])
        await db.commit()
        await db.refresh(user1)
        await db.refresh(user2)
        await db.refresh(device)

        # Admin access for both users
        p1 = PermisoUsuarioArtefacto(id_usuario=user1.id, id_artefacto=device.id, nivel_acceso="ADMIN")
        p2 = PermisoUsuarioArtefacto(id_usuario=user2.id, id_artefacto=device.id, nivel_acceso="ADMIN")
        db.add_all([p1, p2])
        await db.commit()

        print("Testing enviar_push_a_duenos logic and persistent DB logging...")
        # Mock send_push_notification to avoid Expo server hits during test
        import app.push_service
        # Stub the function to return True (token is valid)
        original_send_push = app.push_service.send_push_notification
        async def mock_send_push(token, title, body, extra=None):
            print(f"  [Mock Push] Sent successfully to token: {token[:30]}")
            return True
        app.push_service.send_push_notification = mock_send_push

        try:
            # Trigger push notification
            await enviar_push_a_duenos(
                db,
                mac="11:22:33:44:55:66",
                title="🚨 Alerta Crítica BMS",
                body="Apagado de emergencia por alta temperatura",
            )
            
            # Verify notifications were logged in the database for both users
            notifs_user1 = await obtener_notificaciones_usuario(db, user1.id)
            notifs_user2 = await obtener_notificaciones_usuario(db, user2.id)

            assert len(notifs_user1) == 1, "User 1 should have exactly 1 notification logged in DB"
            assert len(notifs_user2) == 1, "User 2 (without token) should also have exactly 1 notification logged in DB"
            
            assert notifs_user1[0].titulo == "🚨 Alerta Crítica BMS"
            assert notifs_user1[0].cuerpo == "Apagado de emergencia por alta temperatura"
            assert notifs_user1[0].leido == False
            assert notifs_user1[0].eliminado == False
            print("  \033[92m[OK] Successfully verified database logging for owners with/without tokens!\033[0m")

            print("Testing notification read and soft-delete updates...")
            # Mark read
            updated = await actualizar_notificacion_usuario(
                db, notifs_user1[0].id, user1.id, {"leido": True}
            )
            assert updated.leido == True, "Notification should be marked read"

            # Delete single notification
            deleted = await actualizar_notificacion_usuario(
                db, notifs_user1[0].id, user1.id, {"eliminado": True}
            )
            assert deleted.eliminado == True, "Notification should be soft-deleted"

            # Retrieve active notifications again
            active_notifs = await obtener_notificaciones_usuario(db, user1.id)
            assert len(active_notifs) == 0, "Query should return 0 active notifications after delete"
            print("  \033[92m[OK] Successfully verified single notification soft-delete!\033[0m")

            print("Testing clear all notifications action...")
            # Send another notification to User 2
            await enviar_push_a_duenos(
                db,
                mac="11:22:33:44:55:66",
                title="⏰ Automatización",
                body="El dispositivo se ha apagado.",
            )
            notifs_before = await obtener_notificaciones_usuario(db, user2.id)
            assert len(notifs_before) == 2, "User 2 should have 2 active notifications"

            # Clear all
            await eliminar_todas_notificaciones_usuario(db, user2.id)
            notifs_after = await obtener_notificaciones_usuario(db, user2.id)
            assert len(notifs_after) == 0, "User 2 should have 0 active notifications after clearing all"
            print("  \033[92m[OK] Successfully verified clear all notifications!\033[0m")

            print("Testing lease terminology updates...")
            # Trigger state change which logs user command event
            await comando_estado_con_lease(db, device.mac, encendido=True, duracion_minutos=5, id_usuario=user1.id)
            
            # Fetch events
            from sqlalchemy import select
            from app.models import EventoUsuario
            stmt = select(EventoUsuario).where(EventoUsuario.id_usuario == user1.id)
            res = await db.execute(stmt)
            event = res.scalars().first()
            assert event is not None
            print(f"  Event logged: {event.razon_disparo}")
            assert "bloqueo por el usuario" in event.razon_disparo
            assert "lease" not in event.razon_disparo.lower()
            print("  \033[92m[OK] Successfully verified lease terminology was changed to 'bloqueo por el usuario'!\033[0m")

            print("\033[92mAll Step 2 notification tests passed successfully!\033[0m")
        finally:
            # Restore original function
            app.push_service.send_push_notification = original_send_push

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(test_notifications())
