# Mock psutil for Android/Termux environments where the real psutil fails
class Process:
    def __init__(self, *args, **kwargs):
        pass
    def children(self, *args, **kwargs):
        return []
    def kill(self, *args, **kwargs):
        pass

def cpu_percent(*args, **kwargs):
    return 0.0

def virtual_memory(*args, **kwargs):
    class Mem:
        percent = 0.0
        total = 0
        used = 0
        available = 0
    return Mem()

def wait_procs(*args, **kwargs):
    return [], []

class TimeoutExpired(Exception):
    pass
class NoSuchProcess(Exception):
    pass
class AccessDenied(Exception):
    pass
