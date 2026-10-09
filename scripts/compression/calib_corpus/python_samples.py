# Calibration/eval corpus for the Section 11.4 compression proof -- real,
# varied Python code (not synthetic/repeated text), covering common
# patterns a coding assistant actually needs to stay sharp on: control
# flow, classes, recursion, generators, error handling, data structures,
# string/file I/O, decorators, and simple algorithms.

SAMPLES = [
    """def binary_search(arr, target):
    lo, hi = 0, len(arr) - 1
    while lo <= hi:
        mid = (lo + hi) // 2
        if arr[mid] == target:
            return mid
        elif arr[mid] < target:
            lo = mid + 1
        else:
            hi = mid - 1
    return -1""",
    """class LinkedList:
    def __init__(self):
        self.head = None

    def append(self, value):
        node = Node(value)
        if not self.head:
            self.head = node
            return
        current = self.head
        while current.next:
            current = current.next
        current.next = node

    def __iter__(self):
        current = self.head
        while current:
            yield current.value
            current = current.next""",
    """def quicksort(arr):
    if len(arr) <= 1:
        return arr
    pivot = arr[len(arr) // 2]
    left = [x for x in arr if x < pivot]
    middle = [x for x in arr if x == pivot]
    right = [x for x in arr if x > pivot]
    return quicksort(left) + middle + quicksort(right)""",
    """import json
from pathlib import Path


def load_config(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config not found: {path}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)""",
    """class Cache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self._store = {}
        self._order = []

    def get(self, key):
        if key not in self._store:
            return None
        self._order.remove(key)
        self._order.append(key)
        return self._store[key]

    def put(self, key, value):
        if key in self._store:
            self._order.remove(key)
        elif len(self._store) >= self.capacity:
            oldest = self._order.pop(0)
            del self._store[oldest]
        self._store[key] = value
        self._order.append(key)""",
    """def fibonacci(n, memo=None):
    if memo is None:
        memo = {}
    if n in memo:
        return memo[n]
    if n <= 1:
        return n
    memo[n] = fibonacci(n - 1, memo) + fibonacci(n - 2, memo)
    return memo[n]""",
    """from contextlib import contextmanager
import time


@contextmanager
def timer(label: str):
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        print(f"{label}: {elapsed:.3f}s")""",
    """def merge_intervals(intervals):
    if not intervals:
        return []
    intervals.sort(key=lambda x: x[0])
    merged = [intervals[0]]
    for start, end in intervals[1:]:
        last_end = merged[-1][1]
        if start <= last_end:
            merged[-1] = (merged[-1][0], max(last_end, end))
        else:
            merged.append((start, end))
    return merged""",
    """class Stack:
    def __init__(self):
        self._items = []

    def push(self, item):
        self._items.append(item)

    def pop(self):
        if not self._items:
            raise IndexError("pop from empty stack")
        return self._items.pop()

    def peek(self):
        return self._items[-1] if self._items else None

    def is_empty(self):
        return len(self._items) == 0""",
    """async def fetch_all(urls, session):
    results = []
    for url in urls:
        try:
            async with session.get(url) as resp:
                results.append(await resp.json())
        except Exception as exc:
            results.append({"error": str(exc), "url": url})
    return results""",
]
