import threading
from collections.abc import Callable
from datetime import datetime

log_lock = threading.Lock()


def log(msg: str):
    with log_lock:
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


def get_input(
    prompt_lines: list[str], input_msg: str, validator: Callable[[str], bool]
) -> str | None:
    while True:
        for line in prompt_lines:
            print(line)
        choice = input(input_msg)

        if choice.lower() == "q":
            return None
        if validator(choice):
            return choice
        log("❌ 输入不合法，请重新输入！")
