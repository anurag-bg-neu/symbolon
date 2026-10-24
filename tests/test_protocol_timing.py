"""Task 8.3 / Requirement 7.3: a full check takes well under 3 s of compute."""

import time

from core.models import MAX_SET
from tests.helpers import make_person, run_pair


def test_full_check_at_max_set_under_3_seconds(lab):
    genes_a = [f"SYN{i:02d}" for i in range(1, MAX_SET + 1)]
    genes_b = [f"SYN{i:02d}" for i in range(11, MAX_SET + 11)]
    a = make_person(lab, "a", genes_a)
    b = make_person(lab, "b", genes_b)
    for mode in ("detail", "count"):
        start = time.perf_counter()
        ra, rb, _, _ = run_pair(lab, a, b, mode)
        elapsed = time.perf_counter() - start
        assert ra == rb and ra.shared_count == 10
        assert elapsed < 3.0, f"{mode} took {elapsed:.3f}s"
