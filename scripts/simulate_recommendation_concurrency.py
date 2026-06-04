import asyncio
import logging
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout
)
logger = logging.getLogger("simulator")

# --- Mock DB State ---
class MockMariaDB:
    def __init__(self):
        # Target device state
        self.device_id = 1
        self.device_mac = "AA:BB:CC:11:22:33"
        self.estado_deseado = True
        self.estado_reportado = True
        self.auto_kill_at = None
        self.ai_override_until = None
        
        # User settings
        self.ai_control_habilitado = True
        self.auto_apagado_low_priority = False
        
        # Recommendations database table simulation
        self.recomendaciones = [] # list of dicts
        
        # Named advisory locks dictionary
        self.active_locks = {}  # lock_name -> holder_worker_id

    def get_lock(self, lock_name: str, worker_id: int) -> int:
        """Simulates MariaDB SELECT GET_LOCK(name, 0)"""
        if lock_name in self.active_locks:
            if self.active_locks[lock_name] == worker_id:
                return 1
            return 0
        self.active_locks[lock_name] = worker_id
        return 1

    def release_lock(self, lock_name: str, worker_id: int) -> int:
        """Simulates MariaDB SELECT RELEASE_LOCK(name)"""
        if lock_name in self.active_locks:
            if self.active_locks[lock_name] == worker_id:
                del self.active_locks[lock_name]
                return 1
            return 0
        return 0

# Shared global simulated database instance
db = MockMariaDB()

# --- Mock Recommendation Check Methods ---

async def crear_recomendacion_si_necesario(tipo_recomendacion: str, mensaje: str) -> bool:
    """Simulates crud.crear_recomendacion_si_necesario without advisory/row locks"""
    # Check if active recommendation already exists
    existente = any(
        r for r in db.recomendaciones
        if r["tipo"] == tipo_recomendacion and not r["resuelto"]
    )
    # Simulate database latency
    await asyncio.sleep(0.05)
    if existente:
        return False
        
    # Not found, insert new recommendation
    db.recomendaciones.append({
        "tipo": tipo_recomendacion,
        "mensaje": mensaje,
        "resuelto": False
    })
    return True

# --- Mock Workers without Leader Election ---
class SimulatedWorkerWithoutLock:
    def __init__(self, worker_id: int):
        self.worker_id = worker_id

    async def evaluate_device(self):
        # 1. Evaluate sustained risky consumption
        rec_created = await crear_recomendacion_si_necesario(
            "consumo_riesgo_sostenido",
            "Sustained risky consumption detected."
        )
        if rec_created:
            logger.info(f"[NOTIF] Worker {self.worker_id}: Created recommendation row in database")

        # 2. Simulate _handle_ai_control logic
        if db.ai_control_habilitado and not db.auto_kill_at:
            # Simulate DB read/write gap
            await asyncio.sleep(0.05)
            db.auto_kill_at = "in_5_minutes"
            logger.info(f"[NOTIF] Worker {self.worker_id}: Sent AI Warning push notification (Apagado IA Programado)")


# --- Mock Workers with Leader Election ---
class LeaderElection:
    def __init__(self, worker_id: int):
        self.worker_id = worker_id
        self.is_leader = False

    async def acquire(self) -> bool:
        val = db.get_lock("smartsaver_leader_lock", self.worker_id)
        if val == 1:
            self.is_leader = True
            return True
        return False


class SimulatedWorkerWithLock:
    def __init__(self, worker_id: int):
        self.worker_id = worker_id
        self.leader_elect = LeaderElection(worker_id)

    async def run_coordinator(self):
        if not self.leader_elect.is_leader:
            acquired = await self.leader_elect.acquire()
            if acquired:
                logger.info(f"Worker {self.worker_id} elected as LEADER")

        if self.leader_elect.is_leader:
            await self.evaluate_device()

    async def evaluate_device(self):
        # 1. Evaluate recommendation
        rec_created = await crear_recomendacion_si_necesario(
            "consumo_riesgo_sostenido",
            "Sustained risky consumption detected."
        )
        if rec_created:
            logger.info(f"[NOTIF] Worker {self.worker_id}: Created recommendation row in database")

        # 2. AI control warnings
        if db.ai_control_habilitado and not db.auto_kill_at:
            await asyncio.sleep(0.05)
            db.auto_kill_at = "in_5_minutes"
            logger.info(f"[NOTIF] Worker {self.worker_id}: Sent AI Warning push notification (Apagado IA Programado)")



# --- Simulator Runner ---
async def run_simulation():
    global db
    logger.info("=== SCENARIO 1: Current Behavior (4 workers running recommendation engine independently) ===")
    db = MockMariaDB()
    workers_no_lock = [SimulatedWorkerWithoutLock(i) for i in range(1, 5)]

    logger.info("Risky telemetry scan started...")
    # Simulate all 4 workers running the evaluation concurrently
    await asyncio.gather(*(w.evaluate_device() for w in workers_no_lock))
    
    logger.info(f"Total Recommendations created in DB: {len(db.recomendaciones)}")

    print()
    logger.info("=== SCENARIO 2: Proposed Fix (With Leader Election Lock) ===")
    db = MockMariaDB()
    workers_with_lock = [SimulatedWorkerWithLock(i) for i in range(1, 5)]

    logger.info("Risky telemetry scan started...")
    # Simulate all 4 workers running the coordinator
    await asyncio.gather(*(w.run_coordinator() for w in workers_with_lock))
    
    logger.info(f"Total Recommendations created in DB: {len(db.recomendaciones)}")

if __name__ == "__main__":
    asyncio.run(run_simulation())
