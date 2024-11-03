import os
import pickle

from stores.memory_store import MemoryStore


class PickleStore(MemoryStore):
    def __init__(self, pickle_file_path: os.PathLike):
        super().__init__()
        self.__pickle_file_path = pickle_file_path

    @staticmethod
    def create(pickle_file_path: os.PathLike):
        if os.path.exists(pickle_file_path):
            with open(pickle_file_path, "rb") as f:
                return pickle.load(f)
        return PickleStore(pickle_file_path)

    async def pickle(self):
        with open(self.__pickle_file_path, "wb") as f:
            pickle.dump(self, f)