"""Bound pending work as well as threads so failed APIs stop a collection promptly."""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def bounded_map(function, values, max_workers=2):
    remaining = iter(enumerate(values))
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        pending = {}
        def submit_next():
            item = next(remaining, None)
            if item is not None:
                index, value = item
                pending[pool.submit(function, value)] = index
        for _ in range(max_workers):submit_next()
        while pending:
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            # Check the whole completed batch before submitting another call.
            for future in done:results[pending.pop(future)] = future.result()
            for _ in done:submit_next()
    return [results[index] for index in sorted(results)]
