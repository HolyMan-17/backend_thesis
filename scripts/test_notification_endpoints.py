import asyncio
import os
import sys

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# Setup system path to import app package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import Base
from app.main import app, get_db
from app.auth import get_current_user
from app.models import Usuario, NotificacionUsuario

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

async def run_endpoint_tests():
    print("Setting up test database...")
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    
    # SQLite PK autoincrement compatibility fix
    from sqlalchemy import Integer
    NotificacionUsuario.__table__.c.id.type = Integer()
    
    # Create only the tables needed for this test to bypass SQLite composite PK limitations
    tables_to_create = [
        Usuario.__table__,
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
    
    # Mock current authenticated user
    mock_user = Usuario(id=42, auth0_id="auth0|endpointuser", email="endpoints@example.com")
    
    async def override_get_db():
        async with AsyncSessionTest() as session:
            yield session
            
    async def override_get_current_user():
        return mock_user
        
    # Apply dependency overrides
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    from datetime import datetime, timezone, timedelta

    # Pre-populate some notifications in the DB
    async with AsyncSessionTest() as db:
        db.add(mock_user)
        await db.commit()
        await db.refresh(mock_user)
        
        now = datetime.now(timezone.utc)
        n1 = NotificacionUsuario(
            id_usuario=mock_user.id, 
            titulo="Aviso 1", 
            cuerpo="Cuerpo 1",
            timestamp=now - timedelta(minutes=5)
        )
        n2 = NotificacionUsuario(
            id_usuario=mock_user.id, 
            titulo="Aviso 2", 
            cuerpo="Cuerpo 2",
            timestamp=now
        )
        db.add_all([n1, n2])
        await db.commit()
        await db.refresh(n1)
        await db.refresh(n2)
        n1_id = n1.id
        n2_id = n2.id

    client = TestClient(app)
    
    print("Testing GET /api/notifications...")
    response = client.get("/api/notifications")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert data[0]["titulo"] == "Aviso 2" # Descending order
    assert data[1]["titulo"] == "Aviso 1"
    print("  \033[92m[OK] GET /api/notifications returned correct list!\033[0m")
    
    print("Testing PATCH /api/notifications/{id} (mark as read)...")
    response = client.patch(f"/api/notifications/{n1_id}", json={"leido": True})
    assert response.status_code == 200
    data = response.json()
    assert data["leido"] is True
    print("  \033[92m[OK] PATCH /api/notifications/{id} updated read status!\033[0m")
    
    print("Testing DELETE /api/notifications/{id} (soft delete)...")
    response = client.delete(f"/api/notifications/{n2_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "deleted"
    
    # Verify it is no longer listed as active
    response = client.get("/api/notifications")
    assert len(response.json()) == 1
    print("  \033[92m[OK] DELETE /api/notifications/{id} soft-deleted the item successfully!\033[0m")
    
    print("Testing DELETE /api/notifications (clear all)...")
    response = client.delete("/api/notifications")
    assert response.status_code == 200
    assert response.json()["status"] == "all_deleted"
    
    # Verify all are cleared
    response = client.get("/api/notifications")
    assert len(response.json()) == 0
    print("  \033[92m[OK] DELETE /api/notifications cleared all items successfully!\033[0m")

    # Clear overrides
    app.dependency_overrides.clear()
    await engine.dispose()
    print("\033[92mAll Step 3 API endpoint tests passed successfully!\033[0m")

if __name__ == "__main__":
    # TestClient in FastAPI calls async endpoints via asyncio loop under the hood
    asyncio.run(run_endpoint_tests())
