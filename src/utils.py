import random


def shuffle_pair[T](items: list[T]) -> dict[T, T]:
    items = items.copy()
    random.shuffle(items)
    return {items[i]: items[(i + 1) % len(items)] for i in range(len(items))}
