#!/usr/bin/env python3
"""
Cron job script for automatic NovaRouter health checks.
This runs periodically to:
1. Check health of all enabled models
2. Disable dead/unreachable models
3. Log progress and results; leave disabled models untouched
"""

import asyncio
import json
import sys
import logging
from pathlib import Path
from datetime import datetime

# Add project root to path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from nova.database import SessionLocal
from nova.models import Model as ModelRow
from sqlalchemy import select
from sqlalchemy.orm import joinedload
from routers.admin_models import ping_model as probe_model

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(project_root / 'health_check.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

MAX_CONCURRENT_PROBES = 3


async def ping_model(model_row) -> dict:
    """Use the same real probe and status persistence as the admin Ping button."""
    response = await probe_model(str(model_row.id))
    if response.status_code != 200:
        raise RuntimeError(f"Probe for model {model_row.id} returned HTTP {response.status_code}")
    return json.loads(response.body)


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
    
    healthy_count = dead_count = cooling_count = failed_count = disabled_count = completed = 0
    dead_ids: list[int] = []
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_PROBES)

    async def check(model):
        async with semaphore:
            try:
                result = await ping_model(model)
                return model, result, None
            except Exception as err:  # One failing probe must not stop the sweep.
                return model, None, err

    for task in asyncio.as_completed([check(model) for model in models]):
        model, result, error = await task
        completed += 1
        if error is not None:
            failed_count += 1
            logger.error("[%d/%d] %s: probe failed (%s)", completed, total_models, model.exposedId, error)
        elif result["ok"]:
            healthy_count += 1
            logger.info("[%d/%d] %s: healthy (%sms)", completed, total_models, model.exposedId, result.get("latency_ms", 0))
        elif result["status"] == "cooling":
            cooling_count += 1
            logger.info("[%d/%d] %s: cooling", completed, total_models, model.exposedId)
        elif result["status"] == "dead":
            dead_count += 1
            dead_ids.append(model.id)
            logger.info("[%d/%d] %s: dead (HTTP %s)", completed, total_models, model.exposedId, result.get("http_status", 0))
        else:
            failed_count += 1
            logger.warning("[%d/%d] %s: %s", completed, total_models, model.exposedId, result["status"])

    # Only disable models confirmed dead by THIS run, never stale dead rows or
    # models whose probe failed unexpectedly. A concurrent user toggle wins.
    if dead_ids:
        with SessionLocal() as db:
            dead_models = db.scalars(
                select(ModelRow).where(ModelRow.id.in_(dead_ids), ModelRow.status == "dead", ModelRow.enabled.is_(True))
            ).all()
            for model in dead_models:
                model.enabled = False
            disabled_count = len(dead_models)
            db.commit()
            logger.info("Disabled %d freshly checked dead models", disabled_count)
    
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    # Log summary
    logger.info("=" * 50)
    logger.info(f"Health Check Summary:")
    logger.info(f"  Total models checked: {total_models}")
    logger.info(f"  Healthy: {healthy_count}")
    logger.info(f"  Cooling: {cooling_count}")
    logger.info(f"  Dead: {dead_count}")
    logger.info(f"  Failed/unknown: {failed_count}")
    logger.info(f"  Disabled: {disabled_count}")
    logger.info(f"  Duration: {duration:.2f} seconds")
    logger.info(f"  Completed at: {end_time}")
    logger.info("=" * 50)
    
    return {
        "total": total_models,
        "healthy": healthy_count,
        "cooling": cooling_count,
        "dead": dead_count,
        "failed": failed_count,
        "disabled": disabled_count,
        "duration": duration,
    }


async def main():
    """Main function for cron job"""
    try:
        # Run automatic health check
        results = await run_auto_health_check()
        
        return results
        
    except Exception as e:
        logger.error(f"Health check failed: {e}", exc_info=True)
        return {"error": str(e)}


if __name__ == "__main__":
    # For cron job usage
    results = asyncio.run(main())
    
    # A partial sweep should be visible as a failed scheduled run.
    if "error" in results or results.get("failed", 0):
        sys.exit(1)
    sys.exit(0)
