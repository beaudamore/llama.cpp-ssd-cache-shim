"""Tiny leveled logger shared by every module (stdout, one line per event)."""
import time

LEVELS = {"debug": 10, "info": 20, "warn": 30, "error": 40}
_level = 20

def set_level(name):
    global _level
    _level = LEVELS.get(name.lower(), 20)

def log(level, msg):
    if LEVELS[level] >= _level:
        print(time.strftime("%Y-%m-%d %H:%M:%S") + f" shim {level:5s} {msg}", flush=True)
