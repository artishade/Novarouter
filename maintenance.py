"""Periodic maintenance utilities for NovaRouter"""

import asyncio
import logging
from nova.syncengine import sync_providers
from nova.database import SessionLocal
from nova.models import Model as ModelRow
from sqlalchemy import select

async def periodic_maintenance():
    """Every 12 h: sync all providers, then disable dead models."""
    while True:
        await asyncio.sleep(12 * 60 * 60)  # 12 h
        # Sync all enabled providers
        with SessionLocal() as db:
            try:
                await sync_providers(db)
            except Exception as e:
                logging.exception("Periodic provider sync failed: %s", e)
        # Disable unreachable (dead) models
        with SessionLocal() as db:
            dead_models = db.scalars(select(ModelRow).where(ModelRow.status == "dead", ModelRow.enabled.is_(True))).all()
            for m in dead_models:
                m.enabled = False
            if dead_models:
                db.commit()
                logging.info("Periodic maintenance disabled %d dead models", len(dead_models))