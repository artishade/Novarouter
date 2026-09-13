"""Response cache: serve identical chat requests from memory.

Key = sha256 of the canonical JSON of every field that changes the model
output (model, messages, tools, sampling params...). Stream/nova/user fields
are excluded — they never influence the answer.

In-memory LRU with TTL; entries survive only the process lifetime (fine for
a gateway — the win is de-duplicating agent retry/echo traffic, not
persisting answers across restarts).
"""
import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Dict, Optional

from . import config

# fields that never change the model's answer -> excluded from the key
_DROP_KEYS = {"stream", "nova", "user", "seed", "logprobs", "logit_bias"}

_lock = threading.Lock()
_store: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
_hits = 0
_misses = 0


def _cacheable_key(payload: Dict[str, Any]) -> Optional[str]:
    """Canonical cache key, or None when the request must not be cached."""
    if payload.get("stream"):
        return None  # v1: only buffered responses are cached
    nova = payload.get("nova")
    if isinstance(nova, dict) and nova.get("cache") is False:
        return None
    if payload.get("cache") is False:
        return None
    # tools present: outputs depend on tool availability -> too dynamic to cache
    if payload.get("tools"):
        return None
    try:
        canon = json.dumps(
            {k: v for k, v in sorted(payload.items()) if k not in _DROP_KEYS},
            sort_keys=True, ensure_ascii=False,
        )
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def get(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Cache entry for this payload, or None. Records a hit/miss stat."""
    global _hits, _misses
    key = _cacheable_key(payload)
    if key is None:
        return None
    with _lock:
        entry = _store.get(key)
        if entry is not None:
            if entry["expires"] > time.time():
                _hits += 1
                # LRU touch
                _store.move_to_end(key)
                return entry["body"]
            del _store[key]
        _misses += 1
    return None


def put(payload: Dict[str, Any], body: Dict[str, Any], provider: str = "", upstream_model: str = "") -> None:
    key = _cacheable_key(payload)
    if key is None or config.CACHE_TTL <= 0:
        return
    with _lock:
        _prune_locked()
        _store[key] = {
            "body": body,
            "provider": provider,
            "upstream_model": upstream_model,
            "expires": time.time() + config.CACHE_TTL,
        }
        _store.move_to_end(key)
        while len(_store) > max(1, config.CACHE_MAX):
            _store.popitem(last=False)


def _prune_locked() -> None:
    """Drop expired entries. Caller must hold the lock."""
    t = time.time()
    for k in [k for k, v in _store.items() if v["expires"] <= t]:
        del _store[k]


def clear() -> None:
    with _lock:
        _store.clear()


def stats() -> Dict[str, Any]:
    with _lock:
        return {
            "enabled": config.CACHE_TTL > 0,
            "ttl_seconds": config.CACHE_TTL,
            "max_entries": config.CACHE_MAX,
            "entries": len(_store),
            "hits": _hits,
            "misses": _misses,
        }
