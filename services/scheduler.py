from apscheduler.schedulers.asyncio import AsyncIOScheduler

scheduler = AsyncIOScheduler()

async def start_scheduler():
    scheduler.start()
    # scheduler.add_job(...) 