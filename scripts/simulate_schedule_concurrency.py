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
        # The target device DB state
        self.device_mac = "AA:BB:CC:11:22:33"
        self.estado_deseado = False
        # The named advisory locks dictionary
        self.active_locks = {}  # lock_name -> holder_worker_id

    def get_lock(self, lock_name: str, worker_id: int) -> int:
        """Simulates MariaDB SELECT GET_LOCK(name, 0)"""
        if lock_name in self.active_locks:
            if self.active_locks[lock_name] == worker_id:
                return 1  # Already holds the lock
            return 0  # Lock is held by another worker
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

# --- Mock Workers without Leader Election ---
class SimulatedWorkerWithoutLock:
    def __init__(self, worker_id: int):
        self.worker_id = worker_id
        self._last_schedule_states = {}  # Independent in-memory cache for each worker

    async def evaluate_schedule(self, should_be_on: bool):
        # Simulate checking if the schedule state has transitioned
        prev_info = self._last_schedule_states.get("device_1")

        if prev_info is None:
            # Case 1: First run / startup sync
            if should_be_on != db.estado_deseado:
                await self.ejecutar_transicion(should_be_on)
            self._last_schedule_states["device_1"] = {"should_be_on": should_be_on}
        else:
            prev_should_be_on = prev_info["should_be_on"]
            if should_be_on != prev_should_be_on:
                await self.ejecutar_transicion(should_be_on)
            self._last_schedule_states["device_1"]["should_be_on"] = should_be_on

    async def ejecutar_transicion(self, encendido: bool):
        # Simulate DB update
        db.estado_deseado = encendido
        # Simulate pushing notification
        accion = "Encendido" if encendido else "Apagado"
        logger.info(f"[NOTIF] Notification Sent from Worker {self.worker_id}: Automatizacion: {accion}")



# --- Mock Workers with Leader Election ---
class LeaderElection:
    def __init__(self, worker_id: int):
        self.worker_id = worker_id
        self.is_leader = False

    async def acquire(self) -> bool:
        # Simulate acquiring lock
        val = db.get_lock("smartsaver_leader_lock", self.worker_id)
        if val == 1:
            self.is_leader = True
            return True
        return False

    async def release(self):
        if self.is_leader:
            db.release_lock("smartsaver_leader_lock", self.worker_id)
            self.is_leader = False


class SimulatedWorkerWithLock:
    def __init__(self, worker_id: int):
        self.worker_id = worker_id
        self.leader_elect = LeaderElection(worker_id)
        self._last_schedule_states = {}  # Only the leader will run this

    async def run_coordinator(self, should_be_on: bool):
        # Simulate leader election check
        if not self.leader_elect.is_leader:
            acquired = await self.leader_elect.acquire()
            if acquired:
                logger.info(f"Worker {self.worker_id} elected as LEADER")

        if self.leader_elect.is_leader:
            await self.evaluate_schedule(should_be_on)

    async def evaluate_schedule(self, should_be_on: bool):
        prev_info = self._last_schedule_states.get("device_1")

        if prev_info is None:
            if should_be_on != db.estado_deseado:
                await self.ejecutar_transicion(should_be_on)
            self._last_schedule_states["device_1"] = {"should_be_on": should_be_on}
        else:
            prev_should_be_on = prev_info["should_be_on"]
            if should_be_on != prev_should_be_on:
                await self.ejecutar_transicion(should_be_on)
            self._last_schedule_states["device_1"]["should_be_on"] = should_be_on

    async def ejecutar_transicion(self, encendido: bool):
        db.estado_deseado = encendido
        accion = "Encendido" if encendido else "Apagado"
        logger.info(f"[NOTIF] Notification Sent from Worker {self.worker_id}: Automatizacion: {accion}")



# --- Simulator Runner ---
async def run_simulation():
    global db
    logger.info("=== SCENARIO 1: Current Behavior (4 workers running scheduling engine independently) ===")
    db = MockMariaDB()
    workers_no_lock = [SimulatedWorkerWithoutLock(i) for i in range(1, 5)]
    # Seed cache to simulate Case 3: normal runtime boundary crossing
    for w in workers_no_lock:
        w._last_schedule_states["device_1"] = {"should_be_on": False}

    logger.info("Clock reaches boundary (Device should turn ON)")
    await asyncio.gather(*(w.evaluate_schedule(should_be_on=True) for w in workers_no_lock))

    print()
    logger.info("=== SCENARIO 2: Proposed Fix (With Leader Election Lock) ===")
    db = MockMariaDB()
    workers_with_lock = [SimulatedWorkerWithLock(i) for i in range(1, 5)]
    for w in workers_with_lock:
        w._last_schedule_states["device_1"] = {"should_be_on": False}

    logger.info("Clock reaches boundary (Device should turn ON)")
    await asyncio.gather(*(w.run_coordinator(should_be_on=True) for w in workers_with_lock))


if __name__ == "__main__":
    asyncio.run(run_simulation())
