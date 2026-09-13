"""CRUD helpers + upstream key rotation + model routing resolution.

v2: capability persistence, per-client rate limits (RPM) and daily token
quotas (TPD), usage tracking hooks.
v2.1: model fallback routes — map a client-facing model id to an ordered
fallback chain, plus auto-pick of a healthy stand-in when the requested
model is unusable, so agents keep working through model outages.
"""

import itertools
import json
import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from . import adapters, config, db

_rr_lock = threading.Lock()
_rr_counters: Dict[int, itertools.count] = {}


def now() -> float:
    return time.time()


# --------------------------------------------------------------------------
# providers
# --------------------------------------------------------------------------

def list_providers(include_keys: bool = False) -> List[Dict[str, Any]]:
    rows = db.query("SELECT * FROM providers ORDER BY id")
    for r in rows:
        r["key_count"] = db.one(
            "SELECT COUNT(*) c FROM upstream_keys WHERE provider_id=?", (r["id"],)
        )["c"]
        r["active_key_count"] = db.one(
            "SELECT COUNT(*) c FROM upstream_keys WHERE provider_id=? AND enabled=1", (r["id"],)
        )["c"]
        r["model_count"] = db.one(
            "SELECT COUNT(*) c FROM models WHERE provider_id=?", (r["id"],)
        )["c"]
        if include_keys:
            r["keys"] = list_upstream_keys(r["id"])
    return rows


def get_provider(pid: int) -> Optional[Dict[str, Any]]:
    return db.one("SELECT * FROM providers WHERE id=?", (pid,))


def add_provider(
    name: str,
    base_url: str,
    kind: str = "openai",
    prefix: str = "",
    extra_headers: Optional[Dict[str, str]] = None,
    enabled: bool = True,
    priority: int = 100,
) -> int:
    if kind not in adapters.KNOWN_KINDS:
        raise ValueError(f"kind must be one of {adapters.KNOWN_KINDS}")
    return db.execute(
        """INSERT INTO providers (name, base_url, kind, prefix, enabled, extra_headers, priority, created_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            name.strip(),
            base_url.strip().rstrip("/"),
            kind,
            (prefix or "").strip(),
            1 if enabled else 0,
            json.dumps(extra_headers or {}),
            max(1, int(priority or 100)),
            now(),
        ),
    )


def update_provider(pid: int, **fields) -> None:
    allowed = {"name", "base_url", "kind", "prefix", "enabled", "extra_headers", "priority"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "kind" and v not in adapters.KNOWN_KINDS:
            raise ValueError(f"kind must be one of {adapters.KNOWN_KINDS}")
        if k == "enabled":
            v = 1 if v else 0
        if k == "priority":
            v = max(1, int(v))
        if k == "extra_headers" and isinstance(v, dict):
            v = json.dumps(v)
        if k == "base_url":
            v = str(v).strip().rstrip("/")
        sets.append(f"{k}=?")
        params.append(v)
    if not sets:
        return
    params.append(pid)
    db.execute(f"UPDATE providers SET {', '.join(sets)} WHERE id=?", tuple(params))


def delete_provider(pid: int) -> None:
    db.execute("DELETE FROM providers WHERE id=?", (pid,))


# --------------------------------------------------------------------------
# upstream keys
# --------------------------------------------------------------------------

def mask(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 12:
        return key[:3] + "***"
    return f"{key[:8]}...{key[-4:]}"


def list_upstream_keys(provider_id: Optional[int] = None, reveal: bool = False) -> List[Dict[str, Any]]:
    if provider_id:
        rows = db.query("SELECT * FROM upstream_keys WHERE provider_id=? ORDER BY id", (provider_id,))
    else:
        rows = db.query("SELECT * FROM upstream_keys ORDER BY provider_id, id")
    t = now()
    for r in rows:
        r["masked"] = mask(r["api_key"])
        r["cooling"] = r["cooldown_until"] > t
        r["cooldown_left"] = max(0, int(r["cooldown_until"] - t))
        if not reveal:
            r.pop("api_key", None)
    return rows


def add_upstream_key(provider_id: int, api_key: str, label: str = "", weight: int = 1) -> int:
    if not get_provider(provider_id):
        raise ValueError("provider not found")
    key = (api_key or "").strip()
    if not key:
        raise ValueError("api_key cannot be empty")
    return db.execute(
        """INSERT INTO upstream_keys (provider_id, label, api_key, weight, enabled, created_at)
           VALUES (?,?,?,?,1,?)""",
        (provider_id, label.strip(), key, max(1, int(weight)), now()),
    )


def parse_keys(raw: str) -> List[str]:
    """Split a pasted blob into individual keys (newline / comma separated)."""
    if not raw:
        return []
    out, seen = [], set()
    for chunk in raw.replace(",", "\n").splitlines():
        key = chunk.strip()
        if key and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def add_upstream_keys_bulk(provider_id: int, raw: str, label_prefix: str = "key") -> List[int]:
    """Add every key found in a pasted blob. Returns the ids that were created."""
    ids = []
    for i, key in enumerate(parse_keys(raw)):
        try:
            ids.append(add_upstream_key(provider_id, key, f"{label_prefix}{i + 1}"))
        except ValueError:
            continue
    return ids


def update_upstream_key(kid: int, **fields) -> None:
    allowed = {"label", "api_key", "weight", "enabled", "cooldown_until", "last_error"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "enabled":
            v = 1 if v else 0
        if k == "weight":
            v = max(1, int(v))
        sets.append(f"{k}=?")
        params.append(v)
    if not sets:
        return
    params.append(kid)
    db.execute(f"UPDATE upstream_keys SET {', '.join(sets)} WHERE id=?", tuple(params))


def delete_upstream_key(kid: int) -> None:
    db.execute("DELETE FROM upstream_keys WHERE id=?", (kid,))


def clear_cooldowns(provider_id: Optional[int] = None) -> None:
    if provider_id:
        db.execute(
            "UPDATE upstream_keys SET cooldown_until=0, last_error='' WHERE provider_id=?",
            (provider_id,),
        )
    else:
        db.execute("UPDATE upstream_keys SET cooldown_until=0, last_error=''")


def pick_keys(provider_id: int, limit: int = config.MAX_KEY_ATTEMPTS) -> List[Dict[str, Any]]:
    """Round-robin ordered, cooldown-aware key list for a provider.

    Non-cooling keys come first (rotated), then cooling ones as last-resort
    fallback so a request never hard-fails just because every key is cooling.
    """
    rows = db.query(
        "SELECT * FROM upstream_keys WHERE provider_id=? AND enabled=1 ORDER BY id",
        (provider_id,),
    )
    if not rows:
        return []

    # expand by weight
    pool: List[Dict[str, Any]] = []
    for r in rows:
        pool.extend([r] * max(1, r["weight"]))

    with _rr_lock:
        counter = _rr_counters.setdefault(provider_id, itertools.count())
        offset = next(counter) % len(pool)
    rotated = pool[offset:] + pool[:offset]

    t = now()
    fresh, cooling, seen = [], [], set()
    for r in rotated:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        (fresh if r["cooldown_until"] <= t else cooling).append(r)
    return (fresh + cooling)[:limit]


def penalize_key(key_id: int, status: int, error: str) -> None:
    cooldown = 0
    if status == 429:
        cooldown = config.COOLDOWN_429
    elif status == 402:
        cooldown = config.COOLDOWN_402
    elif status >= 500 or status == 0:
        cooldown = config.COOLDOWN_5XX
    elif status in (401, 403):
        cooldown = config.COOLDOWN_402
    db.execute(
        """UPDATE upstream_keys
           SET err_count = err_count + 1,
               last_error = ?,
               cooldown_until = ?
           WHERE id=?""",
        (f"{status}: {error}"[:250], now() + cooldown if cooldown else 0, key_id),
    )


def reward_key(key_id: int) -> None:
    db.execute(
        """UPDATE upstream_keys
           SET req_count = req_count + 1, last_used_at = ?, cooldown_until = 0, last_error=''
           WHERE id=?""",
        (now(), key_id),
    )


# --------------------------------------------------------------------------
# client (gateway) keys
# --------------------------------------------------------------------------

def list_client_keys(reveal: bool = False) -> List[Dict[str, Any]]:
    rows = db.query("SELECT * FROM client_keys ORDER BY id")
    for r in rows:
        r["masked"] = mask(r["token"])
        if not reveal:
            r.pop("token", None)
    return rows


def create_client_key(
    name: str, allowed_models: str = "", rpm_limit: int = 0, tpd_limit: int = 0
) -> Dict[str, Any]:
    token = "nova-" + secrets.token_urlsafe(32)
    kid = db.execute(
        """INSERT INTO client_keys (name, token, enabled, allowed_models, rpm_limit, tpd_limit, created_at)
           VALUES (?,?,1,?,?,?,?)""",
        (
            name.strip() or "unnamed",
            token,
            (allowed_models or "").strip(),
            max(0, int(rpm_limit or 0)),
            max(0, int(tpd_limit or 0)),
            now(),
        ),
    )
    return {
        "id": kid,
        "name": name,
        "token": token,
        "allowed_models": (allowed_models or "").strip(),
        "rpm_limit": int(rpm_limit or 0),
        "tpd_limit": int(tpd_limit or 0),
    }


def update_client_key(kid: int, **fields) -> None:
    allowed = {"name", "enabled", "allowed_models", "rpm_limit", "tpd_limit"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "enabled":
            v = 1 if v else 0
        if k in ("rpm_limit", "tpd_limit"):
            v = max(0, int(v))
        sets.append(f"{k}=?")
        params.append(v)
    if not sets:
        return
    params.append(kid)
    db.execute(f"UPDATE client_keys SET {', '.join(sets)} WHERE id=?", tuple(params))


def delete_client_key(kid: int) -> None:
    db.execute("DELETE FROM client_keys WHERE id=?", (kid,))


def auth_client(token: str) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    row = db.one("SELECT * FROM client_keys WHERE token=? AND enabled=1", (token,))
    if row:
        db.execute(
            "UPDATE client_keys SET req_count=req_count+1, last_used_at=? WHERE id=?",
            (now(), row["id"]),
        )
    return row


def client_allows(client: Dict[str, Any], model: str) -> bool:
    raw = (client.get("allowed_models") or "").strip()
    if not raw:
        return True
    for pattern in [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]:
        if pattern == "*" or pattern == model:
            return True
        if pattern.endswith("*") and model.startswith(pattern[:-1]):
            return True
    return False


def client_rate_limited(client: Dict[str, Any]) -> Optional[str]:
    """Returns a reason string if this client key is over its limits."""
    rpm = int(client.get("rpm_limit") or 0)
    tpd = int(client.get("tpd_limit") or 0)
    if rpm and db.client_rpm_used(client["id"]) >= rpm:
        return f"rate limit exceeded ({rpm} req/min)"
    if tpd and db.client_tpd_used(client["id"]) >= tpd:
        return f"daily token quota exceeded ({tpd} tokens/day)"
    return None


# --------------------------------------------------------------------------
# models / routing
# --------------------------------------------------------------------------

def exposed_id(provider: Dict[str, Any], model_id: str) -> str:
    prefix = (provider.get("prefix") or "").strip()
    return f"{prefix}{model_id}" if prefix else model_id


def _caps_to_json(caps: Any) -> str:
    if isinstance(caps, dict):
        return json.dumps({k: bool(v) for k, v in caps.items()})
    return "{}"


def upsert_models(provider_id: int, models: List[Dict[str, Any]], prune: bool = True) -> int:
    provider = get_provider(provider_id)
    if not provider:
        raise ValueError("provider not found")
    rows = [
        (
            provider_id, m["id"], exposed_id(provider, m["id"]),
            1 if m.get("is_free") else 0,
            int(m.get("context_length") or 0),
            int(m.get("max_output") or 0),
            _caps_to_json(m.get("capabilities")),
        )
        for m in models
    ]
    db.executemany(
        """INSERT INTO models (provider_id, model_id, exposed_id, is_free, context_length, max_output, capabilities)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(provider_id, model_id)
           DO UPDATE SET exposed_id=excluded.exposed_id, is_free=excluded.is_free,
                         context_length=excluded.context_length, max_output=excluded.max_output,
                         capabilities=excluded.capabilities""",
        rows,
    )
    if prune and rows:
        ids = [m["id"] for m in models]
        placeholders = ",".join("?" * len(ids))
        db.execute(
            f"DELETE FROM models WHERE provider_id=? AND model_id NOT IN ({placeholders})",
            tuple([provider_id] + ids),
        )
    return len(rows)


def list_models(
    provider_id: Optional[int] = None,
    status: Optional[str] = None,
    only_enabled: bool = False,
    free_only: bool = False,
    search: str = "",
    capability: str = "",
) -> List[Dict[str, Any]]:
    sql = """SELECT m.*, p.name AS provider_name, p.kind AS provider_kind, p.enabled AS provider_enabled
             FROM models m JOIN providers p ON p.id = m.provider_id WHERE 1=1"""
    params: List[Any] = []
    if provider_id:
        sql += " AND m.provider_id=?"
        params.append(provider_id)
    if status:
        sql += " AND m.status=?"
        params.append(status)
    if only_enabled:
        sql += " AND m.enabled=1 AND p.enabled=1"
    if free_only:
        sql += " AND m.is_free=1"
    if search:
        sql += " AND m.exposed_id LIKE ?"
        params.append(f"%{search}%")
    if capability:
        sql += " AND m.capabilities LIKE ?"
        params.append(f'%"{capability}": true%')
    sql += " ORDER BY p.name, m.model_id"
    return db.query(sql, tuple(params))


def model_capabilities(row: Dict[str, Any]) -> Dict[str, bool]:
    raw = row.get("capabilities") or "{}"
    try:
        data = json.loads(raw)
        return {k: bool(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def set_model_status(
    model_row_id: int, status: str, http_status: int, detail: str, latency_ms: int
) -> None:
    db.execute(
        """UPDATE models SET status=?, http_status=?, detail=?, latency_ms=?, checked_at=?
           WHERE id=?""",
        (status, http_status, (detail or "")[:250], latency_ms, now(), model_row_id),
    )


def toggle_model(model_row_id: int, enabled: bool) -> None:
    db.execute("UPDATE models SET enabled=? WHERE id=?", (1 if enabled else 0, model_row_id))


def resolve_model(requested: str) -> List[Tuple[Dict[str, Any], str]]:
    """Map a client-facing model id to candidate (provider, upstream_model_id).

    Resolution order:
      1. exact exposed_id match on an enabled model+provider
      2. exact raw model_id match
      3. "providername/model" convention
      4. model suffix stripped (':thinking', ':free', ...) with suffix reattached
         so upstream gateways that understand them (OpenRouter) still work.
    Multiple hits = failover candidates, healthiest first.
    """
    # 4. suffix handling first: route on the bare id, remember the suffix
    base, suffix = adapters.split_model_suffix(requested)
    lookup = base if suffix else requested

    rows = db.query(
        """SELECT m.*, p.name AS provider_name FROM models m
           JOIN providers p ON p.id=m.provider_id
           WHERE m.enabled=1 AND p.enabled=1 AND (m.exposed_id=? OR m.model_id=?)""",
        (lookup, lookup),
    )

    if not rows and "/" in lookup:
        head, tail = lookup.split("/", 1)
        rows = db.query(
            """SELECT m.*, p.name AS provider_name FROM models m
               JOIN providers p ON p.id=m.provider_id
               WHERE m.enabled=1 AND p.enabled=1
                 AND LOWER(p.name)=LOWER(?) AND m.model_id=?""",
            (head, tail),
        )

    # provider priority map for ordering (lower number = tried earlier)
    prio_map = {
        p["id"]: int(p.get("priority") or 100)
        for p in db.query("SELECT id, priority FROM providers")
    }

    rank = {adapters.OK: 0, "UNKNOWN": 1, adapters.RATE_LIMITED: 2}
    # provider priority first (lower = tried earlier), then health, then latency
    rows.sort(key=lambda r: (
        int((prio_map or {}).get(r["provider_id"], 100)),
        rank.get(r["status"], 3),
        r["latency_ms"] or 9999,
    ))

    out = []
    for r in rows:
        provider = get_provider(r["provider_id"])
        if provider:
            # re-attach the OpenRouter-style suffix for openai-kind upstreams;
            # anthropic natively thinks on every claude-3.7+ model.
            upstream_id = r["model_id"] + (suffix if suffix and provider.get("kind") == "openai" else "")
            out.append((provider, upstream_id))
    return out


# --------------------------------------------------------------------------
# model fallback routes
# --------------------------------------------------------------------------

def _chain_list(raw: Any) -> List[str]:
    """'a, b\nc' -> ['a','b','c'] — ordered, deduped."""
    out: List[str] = []
    if not raw:
        return out
    text = raw if isinstance(raw, str) else ",".join(str(x) for x in raw)
    for part in text.replace("\n", ",").replace(";", ",").split(","):
        t = part.strip()
        if t and t not in out:
            out.append(t)
    return out


def list_routes() -> List[Dict[str, Any]]:
    rows = db.query("SELECT * FROM model_routes ORDER BY public_id")
    for r in rows:
        r["fallback_list"] = _chain_list(r["fallbacks"])
        r["auto"] = bool(r["auto"])
        r["enabled"] = bool(r["enabled"])
    return rows


def add_route(public_id: str, fallbacks: str, auto: bool = True, note: str = "") -> int:
    public_id = (public_id or "").strip()
    if not public_id:
        raise ValueError("public_id is required ('*' = the default chain for every model)")
    chain = _chain_list(fallbacks)
    if not chain:
        raise ValueError("at least one fallback model id is required")
    return db.execute(
        """INSERT INTO model_routes (public_id, fallbacks, auto, enabled, note, created_at)
           VALUES (?,?,?,1,?,?)""",
        (public_id, ",".join(chain), 1 if auto else 0, (note or "").strip()[:250], now()),
    )


def update_route(rid: int, **fields) -> None:
    allowed = {"public_id", "fallbacks", "auto", "enabled", "note"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        if k == "public_id":
            v = str(v).strip()
            if not v:
                raise ValueError("public_id cannot be empty")
        if k == "fallbacks":
            chain = _chain_list(v)
            if not chain:
                raise ValueError("at least one fallback model id is required")
            v = ",".join(chain)
        if k in ("auto", "enabled"):
            v = 1 if v else 0
        sets.append(f"{k}=?")
        params.append(v)
    if not sets:
        return
    params.append(rid)
    db.execute(f"UPDATE model_routes SET {', '.join(sets)} WHERE id=?", tuple(params))


def delete_route(rid: int) -> None:
    db.execute("DELETE FROM model_routes WHERE id=?", (rid,))


def route_for(public_id: str) -> Optional[Dict[str, Any]]:
    """Most specific enabled route: exact id first, then the '*' default."""
    row = db.one("SELECT * FROM model_routes WHERE public_id=? AND enabled=1", (public_id,))
    if row:
        return row
    return db.one("SELECT * FROM model_routes WHERE public_id='*' AND enabled=1")


def fallback_chain(requested: str) -> Tuple[List[str], bool]:
    """Ordered fallback ids for a requested model. Returns (chain, allow_auto).

    A specific route fully governs its model; the '*' default chain is
    appended after it. With no route at all, auto-pick follows the env flag.
    """
    row = route_for(requested)
    if not row:
        return [], bool(config.AUTO_FALLBACK)
    chain = _chain_list(row["fallbacks"])
    if row["public_id"] == "*":
        return chain, bool(row["auto"])
    dflt = db.one("SELECT * FROM model_routes WHERE public_id='*' AND enabled=1")
    if dflt:
        for t in _chain_list(dflt["fallbacks"]):
            if t not in chain:
                chain.append(t)
    return chain, bool(row["auto"])


def _requested_profile(requested: str) -> Dict[str, bool]:
    """Capability profile of the requested id: registry row if known, else inferred."""
    row = db.one(
        "SELECT capabilities FROM models WHERE exposed_id=? OR model_id=? LIMIT 1",
        (requested, requested),
    )
    if row:
        caps = model_capabilities(row)
        if any(caps.values()):
            return caps
    return adapters.infer_capabilities(requested)


def model_row_capabilities(model_id: str) -> Dict[str, bool]:
    """Capability map for a model id as registered in the catalogue (any
    provider), or inferred from the id when unknown. Used by media routing
    to decide whether the selected model can read a request's media."""
    row = db.one(
        """SELECT m.capabilities FROM models m
           JOIN providers p ON p.id = m.provider_id
           WHERE p.enabled = 1 AND (m.exposed_id=? OR m.model_id=?)
           ORDER BY m.enabled DESC LIMIT 1""",
        (model_id, model_id),
    )
    if row:
        caps = model_capabilities(row)
        if any(caps.values()):
            return caps
    return adapters.infer_capabilities(model_id)


def auto_fallback_targets(
    requested: str,
    limit: int = 0,
    need_caps: Optional[Dict[str, bool]] = None,
) -> List[str]:
    """Healthy enabled model ids that can stand in for `requested`, best match first.

    A candidate must match the requested profile's tools/embeddings
    capability (agents break without them); reasoning/vision are preferred.
    Known-bad models (rate-limited, no access, ...) never auto-serve.

    `need_caps` extends the hard requirements — media routing passes the
    capabilities a request's media actually needs (e.g. vision for images)
    so stand-ins can always read the payload.
    """
    prof = _requested_profile(requested)
    if need_caps:
        prof = {**prof, **{k: True for k, v in need_caps.items() if v}}
    rows = db.query(
        """SELECT m.exposed_id, m.capabilities, m.latency_ms, m.status, m.provider_id FROM models m
           JOIN providers p ON p.id=m.provider_id
           WHERE m.enabled=1 AND p.enabled=1 AND m.status IN ('OK','UNKNOWN')
             AND m.exposed_id != ?""",
        (requested,),
    )
    hard = {"tools", "embeddings"}
    hard |= {k for k, v in (need_caps or {}).items() if v}
    soft = {"reasoning", "vision", "audio_in"}
    prio_map = {
        p["id"]: int(p.get("priority") or 100)
        for p in db.query("SELECT id, priority FROM providers")
    }
    scored: List[Tuple[Any, ...]] = []
    for r in rows:
        caps = model_capabilities(r)
        if any(prof.get(k) and not caps.get(k) for k in hard):
            continue
        smiss = sum(1 for k in soft if prof.get(k) and not caps.get(k))
        bad_status = 0 if r["status"] == "OK" else 1
        lat = r["latency_ms"] or 999999
        prio = int(prio_map.get(r["provider_id"], 100))
        scored.append((bad_status, smiss, prio, lat, r["exposed_id"]))
    scored.sort()
    out: List[str] = []
    for t in scored:
        mid = t[4]
        if mid not in out:
            out.append(mid)
    if limit:
        out = out[:limit]
    return out


# --------------------------------------------------------------------------
# stats
# --------------------------------------------------------------------------

def stats() -> Dict[str, Any]:
    t = now()
    return {
        "providers": db.one("SELECT COUNT(*) c FROM providers")["c"],
        "providers_enabled": db.one("SELECT COUNT(*) c FROM providers WHERE enabled=1")["c"],
        "upstream_keys": db.one("SELECT COUNT(*) c FROM upstream_keys")["c"],
        "upstream_keys_cooling": db.one(
            "SELECT COUNT(*) c FROM upstream_keys WHERE cooldown_until > ?", (t,)
        )["c"],
        "client_keys": db.one("SELECT COUNT(*) c FROM client_keys")["c"],
        "models": db.one("SELECT COUNT(*) c FROM models")["c"],
        "models_ok": db.one("SELECT COUNT(*) c FROM models WHERE status='OK'")["c"],
        "models_unknown": db.one("SELECT COUNT(*) c FROM models WHERE status='UNKNOWN'")["c"],
        "routes": db.one("SELECT COUNT(*) c FROM model_routes")["c"],
        "requests_24h": db.one(
            "SELECT COUNT(*) c FROM request_log WHERE ts > ?", (t - 86400,)
        )["c"],
        "errors_24h": db.one(
            "SELECT COUNT(*) c FROM request_log WHERE ts > ? AND status >= 400", (t - 86400,)
        )["c"],
        "tokens_24h": db.one(
            "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) c FROM request_log WHERE ts > ?",
            (t - 86400,),
        )["c"],
        "cache_hits_24h": db.one(
            "SELECT COUNT(*) c FROM request_log WHERE ts > ? AND via='cache'", (t - 86400,)
        )["c"],
        "batches_active": db.one(
            "SELECT COUNT(*) c FROM batches WHERE status IN ('validating','in_progress')",
        )["c"],
    }


def recent_logs(limit: int = 100) -> List[Dict[str, Any]]:
    return db.query(
        """SELECT l.*, p.name AS provider_name, c.name AS client_name
           FROM request_log l
           LEFT JOIN providers p ON p.id = l.provider_id
           LEFT JOIN client_keys c ON c.id = l.client_key_id
           ORDER BY l.id DESC LIMIT ?""",
        (limit,),
    )


# --------------------------------------------------------------------------
# analytics
# --------------------------------------------------------------------------

def usage_timeseries(
    hours: int = 24,
    group_by: str = "hour",
    model: str = "",
    provider_id: Optional[int] = None,
    client_key_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Request log aggregated into time buckets.

    group_by: 'hour' (default) or 'day'. Portable SQL: integer division on
    the unix ts, the same expression idx_reqlog_day uses.
    """
    hours = max(1, min(int(hours or 24), 720))
    width = 3600 if (group_by or "hour") != "day" else 86400
    since = now() - hours * 3600
    sql = f"""
        SELECT (CAST(ts / {width} AS INT)) * {width} AS bucket,
               COUNT(*) AS requests,
               SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) AS errors,
               SUM(CASE WHEN via='cache' THEN 1 ELSE 0 END) AS cache_hits,
               SUM(tokens_in) AS tokens_in,
               SUM(tokens_out) AS tokens_out,
               AVG(latency_ms) AS avg_latency_ms
        FROM request_log
        WHERE ts > ?"""
    params: List[Any] = [since]
    if model:
        sql += " AND model=?"
        params.append(model)
    if provider_id:
        sql += " AND provider_id=?"
        params.append(provider_id)
    if client_key_id:
        sql += " AND client_key_id=?"
        params.append(client_key_id)
    sql += f" GROUP BY bucket ORDER BY bucket ASC"
    rows = db.query(sql, tuple(params))
    for r in rows:
        r["avg_latency_ms"] = int(r.get("avg_latency_ms") or 0)
    return rows


def _top_by(column: str, hours: int) -> List[Dict[str, Any]]:
    """Top-N aggregation over request_log grouped by `column` (model | provider_id | client_key_id)."""
    hours = max(1, min(int(hours or 24), 720))
    since = now() - hours * 3600
    label = {
        "model": "model",
        "provider_id": "p.name",
        "client_key_id": "c.name",
    }[column]
    join = ""
    if column == "provider_id":
        join = "LEFT JOIN providers p ON p.id = request_log.provider_id"
    elif column == "client_key_id":
        join = "LEFT JOIN client_keys c ON c.id = request_log.client_key_id"
    rows = db.query(
        f"""SELECT {label} AS name, COUNT(*) AS requests,
                   SUM(CASE WHEN request_log.status >= 400 THEN 1 ELSE 0 END) AS errors,
                   SUM(request_log.tokens_in + request_log.tokens_out) AS tokens,
                   AVG(request_log.latency_ms) AS avg_latency_ms
            FROM request_log {join}
            WHERE request_log.ts > ? GROUP BY {label} ORDER BY requests DESC LIMIT 10""",
        (since,),
    )
    for r in rows:
        r["name"] = r.get("name") or "(unknown)"
        r["avg_latency_ms"] = int(r.get("avg_latency_ms") or 0)
    return rows


def top_models(hours: int = 24) -> List[Dict[str, Any]]:
    return _top_by("model", hours)


def top_providers(hours: int = 24) -> List[Dict[str, Any]]:
    return _top_by("provider_id", hours)


def top_clients(hours: int = 24) -> List[Dict[str, Any]]:
    return _top_by("client_key_id", hours)


# --------------------------------------------------------------------------
# files
# --------------------------------------------------------------------------

def create_file(client_key_id: int, filename: str, purpose: str, content: bytes) -> Dict[str, Any]:
    fid = "file-" + secrets.token_urlsafe(16)
    db.execute(
        """INSERT INTO files (id, client_key_id, filename, purpose, bytes, content, status, created_at)
           VALUES (?,?,?,?,?,?, 'processed', ?)""",
        (fid, client_key_id, filename.strip() or "upload", (purpose or "").strip(),
         len(content), content, now()),
    )
    return get_file(fid, client_key_id) or {"id": fid}


def get_file(fid: str, client_key_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    row = db.one("SELECT * FROM files WHERE id=?", (fid,))
    if not row:
        return None
    if client_key_id is not None and row.get("client_key_id") not in (None, client_key_id):
        return None  # files belong to the uploading client key
    row.pop("content", None)  # metadata view; content fetched explicitly
    return row


def get_file_content(fid: str, client_key_id: Optional[int] = None) -> Optional[bytes]:
    row = db.one("SELECT content, client_key_id FROM files WHERE id=?", (fid,))
    if not row:
        return None
    if client_key_id is not None and row.get("client_key_id") not in (None, client_key_id):
        return None
    return row.get("content") or b""


def list_files(client_key_id: Optional[int] = None, purpose: str = "") -> List[Dict[str, Any]]:
    sql = "SELECT id, client_key_id, filename, purpose, bytes, status, created_at FROM files WHERE 1=1"
    params: List[Any] = []
    if client_key_id is not None:
        sql += " AND client_key_id=?"
        params.append(client_key_id)
    if purpose:
        sql += " AND purpose=?"
        params.append(purpose)
    sql += " ORDER BY created_at DESC LIMIT 200"
    return db.query(sql, tuple(params))


def delete_file(fid: str, client_key_id: Optional[int] = None) -> bool:
    if not get_file(fid, client_key_id):
        return False
    db.execute("DELETE FROM files WHERE id=?", (fid,))
    return True


def append_file_content(fid: str, chunk: bytes) -> None:
    """Concatenate onto a file's content (batch output building)."""
    row = db.one("SELECT content FROM files WHERE id=?", (fid,))
    if not row:
        return
    existing = row.get("content") or b""
    db.execute(
        "UPDATE files SET content=?, bytes=? WHERE id=?",
        (existing + chunk, len(existing) + len(chunk), fid),
    )


# --------------------------------------------------------------------------
# batches
# --------------------------------------------------------------------------

def create_batch(client_key_id: int, model: str, total: int, input_file_id: str) -> Dict[str, Any]:
    bid = "batch-" + secrets.token_urlsafe(16)
    t = now()
    db.execute(
        """INSERT INTO batches (id, client_key_id, status, model, total, input_file_id,
                                created_at, expires_at)
           VALUES (?,?,?,?,?,?,?,?)""",
        (bid, client_key_id, "in_progress", model.strip(), int(total), input_file_id,
         t, t + 24 * 3600),
    )
    return get_batch(bid) or {"id": bid}


def get_batch(bid: str) -> Optional[Dict[str, Any]]:
    return db.one("SELECT * FROM batches WHERE id=?", (bid,))


def update_batch(bid: str, **fields) -> None:
    allowed = {"status", "done", "failed", "output_file_id", "error", "completed_at"}
    sets, params = [], []
    for k, v in fields.items():
        if k not in allowed or v is None:
            continue
        sets.append(f"{k}=?")
        params.append(v)
    if not sets:
        return
    params.append(bid)
    db.execute(f"UPDATE batches SET {', '.join(sets)} WHERE id=?", tuple(params))


def list_batches(client_key_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
    if client_key_id is not None:
        return db.query(
            "SELECT * FROM batches WHERE client_key_id=? ORDER BY created_at DESC LIMIT ?",
            (client_key_id, min(max(limit, 1), 200)),
        )
    return db.query(
        "SELECT * FROM batches ORDER BY created_at DESC LIMIT ?",
        (min(max(limit, 1), 200),),
    )


def expire_stale_batches() -> int:
    """Mark in-progress batches past their 24h window as expired. Returns count."""
    return db.execute_rowcount(
        """UPDATE batches SET status='expired', error='24h window elapsed'
           WHERE status IN ('validating','in_progress') AND expires_at < ?""",
        (now(),),
    )