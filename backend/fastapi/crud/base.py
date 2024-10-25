from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

class BaseService:
    def __init__(self, db_sync: Session = None, db_async: AsyncSession = None):
        self.db_sync = db_sync
        self.db_async = db_async

    def get_sync_db(self) -> Session:
        if not self.db_sync:
            raise ValueError("db_sync is not initialized")
        return self.db_sync

    def get_async_db(self) -> AsyncSession:
        if not self.db_async:
            raise ValueError("db_async is not initialized")
        return self.db_async