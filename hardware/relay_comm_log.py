"""
Relay MCU ⇄ PC communication log.

A small thread-safe ring buffer that RelaySerial writes every command (TX) and
reply (RX) to. The backend's broadcast loop drains new entries and pushes them
to the UI so the operator can watch the live serial traffic.
"""
import threading
import time
import collections
from typing import Dict, List, Tuple

_LOCK = threading.Lock()
_BUF: "collections.deque[dict]" = collections.deque(maxlen=400)
_SEQ = 0


def record(direction: str, text: str) -> None:
    """Append one line. `direction` is 'TX', 'RX', or 'INFO'."""
    global _SEQ
    text = (text or "").strip()
    if not text:
        return
    with _LOCK:
        _SEQ += 1
        _BUF.append({"seq": _SEQ, "ts": time.time(), "dir": direction, "text": text})


def get_since(after_seq: int) -> Tuple[List[Dict], int]:
    """Return (entries newer than after_seq, latest seq)."""
    with _LOCK:
        items = [dict(e) for e in _BUF if e["seq"] > after_seq]
        last = _BUF[-1]["seq"] if _BUF else after_seq
    return items, last
