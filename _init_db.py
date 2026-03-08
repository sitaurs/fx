import asyncio
from database.repository import Repository

async def main():
    repo = Repository()
    await repo.init_db()
    print("✓ Database initialized successfully")

if __name__ == "__main__":
    asyncio.run(main())
