#!/usr/bin/env python3
"""
Auto health check and toggle script for NovaRouter.
Features:
1. Runs health check on all enabled models
2. Automatically disables unreachable/dead models 
3. Provides option to re-check disabled models
"""

import asyncio
import time
import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from nova.database import SessionLocal
from nova.models import Model as ModelRow, ProviderKey
from sqlalchemy import select
from sqlalchemy.orm import joinedload
import httpx
import random
import json


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


def _snippet(text: str) -> str:
    """Short snippet for logging"""
    text = " ".join(text.split())
    return text[:60] + ("…" if len(text) > 60 else "")


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
            # Built-in engine - skip for now
            return {"ok": True, "status": "healthy", "detail": "Built-in engine - auto healthy"}

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
                            "detail": f'Replied "{_snippet(reply)}" (HTTP {http_status}, {latency_ms}ms)'
                        }
                    else:
                        return {
                            "ok": False,
                            "status": "dead",
                            "latency_ms": latency_ms,
                            "http_status": http_status,
                            "detail": f"Reachable but no reply content (HTTP {http_status})"
                        }
                elif res.status_code == 429:
                    return {
                        "ok": False,
                        "status": "cooling",
                        "latency_ms": latency_ms,
                        "http_status": http_status,
                        "detail": f"Rate limited (HTTP 429) - cooling down"
                    }
                else:
                    return {
                        "ok": False,
                        "status": "dead",
                        "latency_ms": latency_ms,
                        "http_status": http_status,
                        "detail": f"HTTP {http_status} error"
                    }
            except Exception as err:
                latency_ms = int((time.monotonic() - started) * 1000)
                return {
                    "ok": False,
                    "status": "dead",
                    "latency_ms": latency_ms,
                    "http_status": 0,
                    "detail": f"Network error: {str(err)[:120]}"
                }


async def health_check_all_models(include_disabled=False):
    """Run health check on all models"""
    with SessionLocal() as db:
        query = select(ModelRow).options(joinedload(ModelRow.provider))
        if not include_disabled:
            query = query.where(ModelRow.enabled.is_(True))
        
        models = db.scalars(query).all()
    
    print(f"Checking health for {len(models)} models...")
    
    results = []
    for i, model in enumerate(models, 1):
        print(f"[{i}/{len(models)}] Checking {model.exposedId} ({model.provider.name})...", end=" ")
        result = await ping_model(model)
        results.append((model, result))
        
        # Update database
        with SessionLocal() as db:
            db_model = db.get(ModelRow, model.id)
            if db_model:
                db_model.status = result["status"]
                db_model.latencyMs = result.get("latency_ms", 0)
                db_model.httpStatus = result.get("http_status", 0)
                db.commit()
        
        status_emoji = "✅" if result["ok"] else "❌"
        print(f"{status_emoji} {result['status']}")
    
    return results


def disable_dead_models():
    """Disable all models marked as dead"""
    with SessionLocal() as db:
        dead_models = db.scalars(
            select(ModelRow).where(ModelRow.status == "dead", ModelRow.enabled.is_(True))
        ).all()
        
        for model in dead_models:
            model.enabled = False
        
        if dead_models:
            db.commit()
            print(f"Disabled {len(dead_models)} dead models:")
            for model in dead_models:
                print(f"  - {model.exposedId} ({model.provider.name})")
        else:
            print("No dead models to disable")
        
        return dead_models


def recheck_disabled_models():
    """Re-check disabled models and optionally re-enable healthy ones"""
    with SessionLocal() as db:
        disabled_models = db.scalars(
            select(ModelRow).where(ModelRow.enabled.is_(False)).options(joinedload(ModelRow.provider))
        ).all()
    
    print(f"Found {len(disabled_models)} disabled models")
    return disabled_models


async def main():
    """Main function with interactive menu"""
    print("=" * 60)
    print("NovaRouter Health Check & Model Management")
    print("=" * 60)
    
    while True:
        print("\nOptions:")
        print("1. Run health check on all enabled models")
        print("2. Auto-disable dead/unreachable models")
        print("3. List and re-check disabled models")
        print("4. Full automatic cleanup (health check + disable dead)")
        print("5. Exit")
        
        choice = input("\nEnter choice (1-5): ").strip()
        
        if choice == "1":
            print("\nRunning health check on all enabled models...")
            results = await health_check_all_models(include_disabled=False)
            
            healthy = sum(1 for _, r in results if r["ok"])
            dead = sum(1 for _, r in results if r["status"] == "dead")
            cooling = sum(1 for _, r in results if r["status"] == "cooling")
            
            print(f"\nSummary: {healthy} healthy, {dead} dead, {cooling} cooling")
            
        elif choice == "2":
            print("\nDisabling dead/unreachable models...")
            disabled = disable_dead_models()
            
        elif choice == "3":
            print("\nListing disabled models...")
            disabled = recheck_disabled_models()
            
            if disabled:
                print("\nDisabled models:")
                for i, model in enumerate(disabled, 1):
                    print(f"{i}. {model.exposedId} ({model.provider.name}) - Status: {model.status}")
                
                recheck = input("\nRe-check specific models? (enter numbers separated by space, or 'all'): ").strip()
                
                if recheck.lower() == 'all':
                    models_to_check = disabled
                else:
                    indices = []
                    for num in recheck.split():
                        try:
                            idx = int(num) - 1
                            if 0 <= idx < len(disabled):
                                indices.append(idx)
                        except:
                            pass
                    models_to_check = [disabled[i] for i in indices] if indices else []
                
                if models_to_check:
                    print(f"\nRe-checking {len(models_to_check)} model(s)...")
                    for model in models_to_check:
                        print(f"\nChecking {model.exposedId}...", end=" ")
                        result = await ping_model(model)
                        
                        # Update in database
                        with SessionLocal() as db:
                            db_model = db.get(ModelRow, model.id)
                            if db_model:
                                db_model.status = result["status"]
                                db_model.latencyMs = result.get("latency_ms", 0)
                                db_model.httpStatus = result.get("http_status", 0)
                                
                                # Auto-enable if healthy
                                if result["ok"]:
                                    db_model.enabled = True
                                    print(f"✅ Healthy - AUTO-ENABLED")
                                else:
                                    print(f"❌ {result['status']}")
                                db.commit()
            else:
                print("No disabled models found")
                
        elif choice == "4":
            print("\nRunning full automatic cleanup...")
            
            # Step 1: Health check
            print("\nStep 1: Health checking all enabled models...")
            results = await health_check_all_models(include_disabled=False)
            
            healthy = sum(1 for _, r in results if r["ok"])
            dead = sum(1 for _, r in results if r["status"] == "dead")
            
            print(f"  Found: {healthy} healthy, {dead} dead")
            
            # Step 2: Disable dead
            print("\nStep 2: Disabling dead models...")
            disabled = disable_dead_models()
            
            print(f"\n✅ Cleanup complete. Disabled {len(disabled)} model(s)")
            
        elif choice == "5":
            print("\nExiting...")
            break
            
        else:
            print("Invalid choice. Please try again.")


if __name__ == "__main__":
    asyncio.run(main())