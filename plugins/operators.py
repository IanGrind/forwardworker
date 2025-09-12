importimport asyncio
import logging
from config import temp
from database import db
from plugins.test import CLIENT

logger = logging.getLogger(__name__)
OPERATOR_START_TIMEOUT = 30  # Seconds

async def resilient_start_clone(config):
    """Wrapper to start a clone with a timeout."""
    try:
        # We don't need the wake-up routine here as these are persistent clients
        client = CLIENT.client(config)
        await asyncio.wait_for(client.start(), timeout=OPERATOR_START_TIMEOUT)
        return client, None
    except asyncio.TimeoutError:
        error_msg = f"Timed out after {OPERATOR_START_TIMEOUT}s"
        return None, error_msg
    except Exception as e:
        return None, str(e)

async def start_operators():
    """
    Starts all configured operator bots/userbots ONCE and stores them
    in a persistent pool for reuse.
    """
    logger.info("Starting all persistent operator clients...")
    temp.OPERATOR_CLIENTS = {}
    
    # We need a user ID to get the bots, we will use the owner's ID for this global startup
    # This assumes the owner has configured the bots.
    from config import Config
    if not Config.OWNER_ID:
        logger.warning("No OWNER_ID set, cannot start global operator bots.")
        return
        
    user_id = Config.OWNER_ID[0]
    operator_configs = await db.get_bots(user_id)
    
    if not operator_configs:
        logger.info("No operator bots configured to start.")
        return

    start_tasks = [resilient_start_clone(config) for config in operator_configs]
    results = await asyncio.gather(*start_tasks)
    
    successful_count = 0
    for i, (client, error) in enumerate(results):
        bot_name = operator_configs[i].get('name', f"Operator #{i+1}")
        bot_id = operator_configs[i].get('id')
        if client:
            temp.OPERATOR_CLIENTS[bot_id] = client
            successful_count += 1
            logger.info(f"✅ Successfully started and authenticated operator: {bot_name} ({bot_id})")
        else:
            logger.error(f"❌ Failed to start operator {bot_name} ({bot_id}): {error}")
            
    logger.info(f"Operator startup complete. {successful_count}/{len(operator_configs)} clients are running and persistent.")

async def stop_operators():
    """Stops all running operator clients."""
    logger.info("Stopping all persistent operator clients...")
    stop_tasks = [
        client.stop() for client in temp.OPERATOR_CLIENTS.values() if client.is_connected
    ]
    await asyncio.gather(*stop_tasks, return_exceptions=True)
    logger.info("All operator clients have been stopped.")
