import threading
from datetime import datetime

log_lock = threading.Lock()


def log(msg):
    with log_lock:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")
