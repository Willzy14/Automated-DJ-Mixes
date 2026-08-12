"""Two ALS builds in one process must not contaminate each other.

The A/B experiment renders an `interim_v1` mix and a `sam_v1` mix from the same
inputs. If the second build inherits module state from the first, the two are no
longer comparable and a difference could be an artefact rather than a policy
effect. These tests pin the isolation properties that make a same-process
comparison trustworthy.
"""

import apply_automation


def _alloc_run(n: int) -> list[int]:
    apply_automation.reset_automation_ids()
    return [apply_automation._alloc_id() for _ in range(n)]


def test_reset_makes_id_allocation_repeatable():
    """The core property: same call sequence -> same IDs, every time."""
    first = _alloc_run(5)
    second = _alloc_run(5)
    assert first == second


def test_ids_leak_across_builds_without_a_reset():
    """Proves the failure this reset exists to prevent - if this ever stops
    failing, the counter is no longer shared and the reset is redundant."""
    apply_automation.reset_automation_ids()
    first = [apply_automation._alloc_id() for _ in range(5)]
    # Second build in the same process, but nobody called reset:
    leaked = [apply_automation._alloc_id() for _ in range(5)]
    assert leaked != first, "counter no longer shared; revisit this test"
    assert min(leaked) > max(first)


def test_reset_after_a_partial_build_still_restores_the_base():
    """A build that raised part-way must not shift the next build's IDs."""
    baseline = _alloc_run(3)
    apply_automation.reset_automation_ids()
    [apply_automation._alloc_id() for _ in range(17)]  # aborted build
    assert _alloc_run(3) == baseline


def test_main_resets_ids_before_building():
    """The reset must actually be wired into the entry point, not just exist."""
    import inspect

    source = inspect.getsource(apply_automation.main)
    assert "reset_automation_ids()" in source
