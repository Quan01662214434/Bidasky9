"""Patch web_server.py - add keep-alive, fix shutdown, add imports"""

with open('web_server.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Track changes
changes = []

# 1. Add asyncio import
if 'import asyncio' not in content:
    content = content.replace(
        'import logging\nfrom datetime import',
        'import logging\nimport asyncio\nfrom datetime import'
    )
    changes.append('asyncio import')

# 2. Add httpx import
if 'import httpx' not in content:
    content = content.replace(
        'from dotenv import load_dotenv\nimport pytz',
        'from dotenv import load_dotenv\nimport httpx\nimport pytz'
    )
    changes.append('httpx import')

# 3. Add keep-alive task variable and loop function
if '_keep_alive_task' not in content:
    old = 'bot_application = None  # Will be initialized on startup\n'
    new = '''bot_application = None  # Will be initialized on startup
_keep_alive_task = None  # Background task to prevent Render from sleeping


async def _keep_alive_loop():
    """Self-ping every 10 min to prevent Render Free Tier from sleeping."""
    base_url = os.getenv("RENDER_EXTERNAL_URL", os.getenv("WEBAPP_URL", ""))
    if not base_url:
        logger.warning("No RENDER_EXTERNAL_URL, keep-alive disabled")
        return
    
    health_url = f"{base_url}/health"
    logger.info("Keep-alive enabled: ping %s every 10 min", health_url)
    
    while True:
        try:
            await asyncio.sleep(600)  # 10 minutes
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(health_url)
                logger.debug("Keep-alive ping: %s", resp.status_code)
        except asyncio.CancelledError:
            logger.info("Keep-alive stopped")
            break
        except Exception as e:
            logger.warning("Keep-alive ping error: %s", e)

'''
    content = content.replace(old, new)
    changes.append('keep-alive loop')

# 4. Update startup to launch keep-alive
if 'global bot_application\n' in content and '_keep_alive_task' in content:
    content = content.replace(
        '    global bot_application\n',
        '    global bot_application, _keep_alive_task\n',
        1  # only first occurrence
    )
    changes.append('startup global')

# 5. Add keep-alive task creation before "BOT DA SAN SANG"
if 'asyncio.create_task(_keep_alive_loop())' not in content:
    content = content.replace(
        '        logger.info("=== BOT',
        '        # Start keep-alive self-ping to prevent Render from sleeping\n        _keep_alive_task = asyncio.create_task(_keep_alive_loop())\n        \n        logger.info("=== BOT'
    )
    changes.append('keep-alive task start')

# 6. Improve shutdown handler
old_shutdown = '''@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup khi server shutdown."""
    global bot_application
    if bot_application:
        try:
            await bot_application.stop()
            await bot_application.shutdown()
        except Exception:
            pass'''

new_shutdown = '''@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup khi server shutdown."""
    global bot_application, _keep_alive_task
    
    # Stop keep-alive
    if _keep_alive_task:
        _keep_alive_task.cancel()
        try:
            await _keep_alive_task
        except asyncio.CancelledError:
            pass
    
    # Stop bot with timeout
    if bot_application:
        try:
            await asyncio.wait_for(bot_application.stop(), timeout=10)
            await asyncio.wait_for(bot_application.shutdown(), timeout=10)
            logger.info("Bot shutdown successfully")
        except asyncio.TimeoutError:
            logger.warning("Bot shutdown timeout, forcing exit")
        except Exception as e:
            logger.error("Bot shutdown error: %s", e)'''

if old_shutdown in content:
    content = content.replace(old_shutdown, new_shutdown)
    changes.append('shutdown handler')

with open('web_server.py', 'w', encoding='utf-8') as f:
    f.write(content)

for c in changes:
    print(f'  + {c}')
print(f'Done! {len(changes)} changes applied.')
