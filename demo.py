"""Live demo: FIFO wakeup order + queue snapshots after timeout/cancel.
Run:  python3 demo.py
"""
import threading
import time

from fair_semaphore import FairSemaphore

sem = FairSemaphore(1)
sem.acquire()  # main thread holds the only permit

cancel_c = threading.Event()
timeouts = {"B": 0.3}          # B will time out
order = []


def worker(name):
    while len(sem) != "ABCDE".index(name):   # deterministic arrival
        time.sleep(0.001)
    ok = sem.acquire(timeout=timeouts.get(name, 5.0),
                     cancel=cancel_c if name == "D" else None,
                     name=name)
    print("  acquire(%s) -> %s" % (name, ok))
    if ok:
        order.append(name)
        sem.release()


threads = [threading.Thread(target=worker, args=(n,)) for n in "ABCDE"]
for t in threads:
    t.start()
time.sleep(0.1)
print("[snapshot] all queued:          ", sem.snapshot())

time.sleep(0.3)   # B times out
print("[snapshot] B timed out:         ", sem.snapshot())

cancel_c.set()    # D cancelled
time.sleep(0.1)
print("[snapshot] D cancelled:         ", sem.snapshot())

print("[release] one release starts the handoff chain A -> C -> E")
sem.release()     # -> A; each worker's own release wakes the next head
for t in threads:
    t.join()

print("[result] wakeup order:", order, "(expected: ['A', 'C', 'E'])")
print("[result] final value:", sem.value, " queue:", sem.snapshot())
