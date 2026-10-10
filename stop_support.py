"""Persistent, cooperative shutdown shared by the GUI and scheduled runner."""
from pathlib import Path
import threading
import time


def wait_until_idle(directory, timeout=120):
    """Wait for a scheduled runner's cleanup to release the shared lock."""
    import msvcrt
    path = Path(directory) / 'morning.lock'
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with path.open('a+b') as mutex:
        while True:
            mutex.seek(0)
            try:
                msvcrt.locking(mutex.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('晨跑尚未完成退出，请检查运动页面；未强制终止正在结束记录的进程。')
                time.sleep(.2)
            else:
                mutex.seek(0)
                msvcrt.locking(mutex.fileno(), msvcrt.LK_UNLCK, 1)
                return


def request_disable(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'disabled.flag').touch()
    (directory / 'stop-all.request').touch()


def resume(directory):
    directory = Path(directory)
    (directory / 'stop-all.request').unlink(missing_ok=True)
    (directory / 'disabled.flag').unlink(missing_ok=True)


def disabled(directory):
    return (Path(directory) / 'disabled.flag').exists()


class StopWatcher:
    def __init__(self, directory, event, private_request=None):
        self.event = event
        self.finished = threading.Event()
        self.paths = [Path(directory) / 'disabled.flag', Path(directory) / 'stop-all.request']
        if private_request:
            self.paths.append(Path(private_request))
        self.thread = threading.Thread(target=self.watch, daemon=True)

    def watch(self):
        while not self.finished.is_set():
            if any(path.exists() for path in self.paths):
                self.event.set()
                return
            if self.event.is_set() or self.finished.wait(.2):
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.finished.set()
        self.thread.join(timeout=1)
