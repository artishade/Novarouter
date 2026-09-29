#!/usr/bin/env python3
"""
Cron job script for automatic NovaRouter health checks.
This runs periodically to:
1. Check health of all enabled models
2. Disable dead/unreachable models
3. Log results
"""

import asyncio
import time
import sys
import logging
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from nova.database import SessionLocal
from nova.models import Model as ModelRow, ProviderKey
from sqlalchemy import select
from sqlalchemy.orm import joinedload
import httpx
import random

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('/root/Novarouter2/Novarouter/health_check.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

PING_TIMEOUT_S = 15.0
PROBE_QUESTIONS = [
    "Reply with exactly one word: OK",
    "What is 2+2? Reply with just the number.",
    "Say hello.",
    "Reply with a single word: pong",
    "Name any color. One word only.",
    "Reply with the word: alive",
]


def _openai_reply(data: dict) -> str:
    """Extract assistant reply from OpenAI format"""
    try:
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = msg.get("content")
        if isinstance(text, list):
            text = " ".join(p.get("text", "") for p in text if isinstance(p, dict))
        return str(text or "").strip()
    except Exception:
        return ""


def _gemini_reply(data: dict) -> str:
    """Extract assistant reply from Gemini format"""
    try:
        cand = (data.get("candidates") or [{}])[0]
        parts = ((cand.get("content") or {}).get("parts")) or []
        return " ".join(str(p.get("text", "")) for p in parts if isinstance(p, dict)).strip()
    except Exception:
        return ""


def _anthropic_reply(data: dict) -> str:
    """Extract assistant reply from Anthropic format"""
    try:
        blocks = data.get("content") or []
        return " ".join(str(b.get("text", "")) for b in blocks if isinstance(b, dict)).strip()
    except Exception:
        return ""


async def ping_model(model_row) -> dict:
    """Health check a single model"""
    with SessionLocal() as db:
        # Refresh the model with relationships
        model = db.scalars(
            select(ModelRow).where(ModelRow.id == model_row.id).options(joinedload(ModelRow.provider))
        ).first()
        if model is None:
            return {"ok": False, "status": "not_found", "detail": "Model not found"}

        provider = model.provider
        keys = db.scalars(
            select(ProviderKey).where(ProviderKey.providerId == provider.id).order_by(ProviderKey.id)
        ).all()
        question = random.choice(PROBE_QUESTIONS)

        if provider.kind == "builtin":
            # Built-in engine - always healthy
            return {"ok": True, "status": "healthy", "detail": "Built-in engine"}

        elif not keys or not provider.baseUrl:
            return {
                "ok": False,
                "status": "dead",
                "detail": "No upstream key configured" if not keys else "Provider has no base URL configured"
            }

        else:
            key = next((k.apiKey for k in keys if k.enabled), keys[0].apiKey)
            base = provider.baseUrl.rstrip("/")
            headers = {"Content-Type": "application/json"}
            url = f"{base}/chat/completions"
            payload = {
                "model": model.modelId,
                "messages": [{"role": "user", "content": question}],
                "max_tokens": 16,
                "temperature": 0,
                "stream": False,
            }
            extract = _openai_reply
            
            if provider.key == "gemini" or provider.kind == "gemini":
                url = f"{base}/models/{model.modelId}:generateContent?key={key}"
                payload = {
                    "contents": [{"parts": [{"text": question}]}],
                    "generationConfig": {"maxOutputTokens": 16, "temperature": 0},
                }
                extract = _gemini_reply
            elif provider.kind == "anthropic":
                url = (f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages")
                headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
                payload = {
                    "model": model.modelId,
                    "max_tokens": 16,
                    "messages": [{"role": "user", "content": question}],
                }
                extract = _anthropic_reply
            else:
                if provider.key not in ("openrouter",) or key:
                    headers["Authorization"] = f"Bearer {key}"

            started = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=PING_TIMEOUT_S) as client:
                    res = await client.post(url, json=payload, headers=headers)
                latency_ms = int((time.monotonic() - started) * 1000)
                http_status = res.status_code
                
                if 200 <= res.status_code < 300:
                    try:
                        data = res.json()
                        reply = extract(data)
                    except:
                        data = {}
                        reply = ""
                    
                    if reply:
                        return {
                            "ok": True,
                            "status": "healthy",
                            "latency_ms": latency_ms,
                            "http_status": http_status,
                        }
                    else:
                        return {
                            "ok": False,
                            "status": "dead",
                            "latency_ms": latency_ms,
                            "http_status": http_status,
                        }
                elif res.status_code == 429:
                    return {
                        "ok": False,
                        "status": "cooling",
                        "latency_ms": latency_ms,
                        "http_status": http_status,
                    }
                else:
                    return {
                        "ok": False,
                        "status": "dead",
                        "latency_ms": latency_ms,
                        "http_status": http_status,
                    }
            except Exception as err:
                latency_ms = int((time.monotonic() - started) * 1000)
                return {
                    "ok": False,
                    "status": "dead",
                    "latency_ms": latency_ms,
                    "http_status": 0,
                }


async def run_auto_health_check():
    """Run automatic health check and disable dead models"""
    start_time = datetime.now()
    logger.info(f"Starting automatic health check at {start_time}")
    
    # Get all enabled models
    with SessionLocal() as db:
        models = db.scalars(
            select(ModelRow)
            .where(ModelRow.enabled.is_(True))
            .options(joinedload(ModelRow.provider))
        ).all()
    
    total_models = len(models)
    logger.info(f"Checking {total_models} enabled models")
    
    # Check each model
    healthy_count = 0
    dead_count = 0
    cooling_count = 0
    
    for i, model in enumerate(models, 1):
        logger.info(f"[{i}/{total_models}] Checking {model.exposedId} ({model.provider.name})...")
        result = await ping_model(model)
        
        # Update database
        with SessionLocal() as db:
            db_model = db.get(ModelRow, model.id)
            if db_model:
                db_model.status = result["status"]
                db_model.latencyMs = result.get("latency_ms", 0)
                db_model.httpStatus = result.get("http_status", 0)
                db.commit()
        
        if result["ok"]:
            healthy_count += 1
            logger.info(f"  ✅ Healthy (latency: {result.get('latency_ms', 0)}ms)")
        elif result["status"] == "cooling":
            cooling_count += 1
            logger.info(f"  ⚠️  Cooling")
        else:
            dead_count += 1
            logger.info(f"  ❌ Dead (HTTP: {result.get('http_status', 0)})")
    
    # Disable dead models
    with SessionLocal() as db:
        dead_models = db.scalars(
            select(ModelRow).where(ModelRow.status == "dead", ModelRow.enabled.is_(True))
        ).all()
        
        disabled_count = 0
        for model in dead_models:
            model.enabled = False
            disabled_count += 1
        
        if disabled_count > 0:
            db.commit()
            logger.info(f"Disabled {disabled_count} dead models")
    
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    # Log summary
    logger.info("=" * 50)
    logger.info(f"Health Check Summary:")
    logger.info(f"  Total models checked: {total_models}")
    logger.info(f"  Healthy: {healthy_count}")
    logger.info(f"  Cooling: {cooling_count}")
    logger.info(f"  Dead: {dead_count}")
    logger.info(f"  Disabled: {disabled_count}")
    logger.info(f"  Duration: {duration:.2f} seconds")
    logger.info(f"  Completed at: {end_time}")
    logger.info("=" * 50)
    
    return {
        "total": total_models,
        "healthy": healthy_count,
        "cooling": cooling_count,
        "dead": dead_count,
        "disabled": disabled_count,
        "duration": duration,
    }


def recheck_disabled_models():
    """Check disabled models and re-enable healthy ones (optional)"""
    logger.info("Checking disabled models for re-enabling...")
    
    with SessionLocal() as db:
        disabled_models = db.scalars(
            select(ModelRow)
            .where(ModelRow.enabled.is_(False))
            .options(joinedload(ModelRow.provider))
        ).all()
    
    if not disabled_models:
        logger.info("No disabled models found")
        return 0
    
    logger.info(f"Found {len(disabled_models)} disabled models")
    
    # Sample checking (adjust as needed)
    sample_size = min(5, len(disabled_models))
    sample = random.sample(disabled_models, sample_size)
    
    reenabled_count = 0
    for model in sample:
        logger.info(f"Checking disabled model: {model.exposedId}")
        # You could run ping_model here and re-enable if healthy
        # For now, just log
    
    return reenabled_count


async def main():
    """Main function for cron job"""
    try:
        # Run automatic health check
        results = await run_auto_health_check()
        
        # Optional: Re-check some disabled models
        if results["total"] > 0:
            reenabled = recheck_disabled_models()
            if reenabled > 0:
                logger.info(f"Re-enabled {reenabled} previously disabled models")
        
        return results
        
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return {"error": str(e)}


if __name__ == "__main__":
    # For cron job usage
    results = asyncio.run(main())
    
    # Exit with non-zero code if there was an error
    if "error" in results:
        sys.exit(1)
    sys.exit(0)