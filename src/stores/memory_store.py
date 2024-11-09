from stores.sqlite_store import SqliteStore


class MemoryStore(SqliteStore):
    def __init__(self):
        super().__init__(":memory:")

