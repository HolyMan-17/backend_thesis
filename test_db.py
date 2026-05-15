import asyncio
from sqlalchemy import text
from app.database import engine

async def probar_conexion():
    print("Iniciando prueba de conexión a MariaDB...")
    try:
        # engine.begin() abre una conexión desde el pool de aiomysql
        async with engine.begin() as conn:
            resultado = await conn.execute(text("SELECT VERSION();"))
            version = resultado.scalar()
            print(f"\033[92m¡Éxito! Conectado a MariaDB. Versión del motor: {version}\033[0m")
    except Exception as e:
        print(f"\033[91mError crítico de conexión:\n{e}\033[0m")
    finally:
        # Destruye el pool de conexiones y libera el puerto antes de salir
        await engine.dispose()

if __name__ == "__main__":
    # asyncio.run() crea el Event Loop, ejecuta la corrutina y cierra el loop al terminar.
    asyncio.run(probar_conexion())
