"""Free the pictures a renderer drew before it returns.

Why: a matplotlib pyplot figure that is saved and closed with plt.close(fig)
stays alive in a reference cycle (figure <-> canvas, figure <-> axes) until
Python's cyclic garbage collector happens to run. The Agg renderer that
rasterised the picture is cached on the canvas, so the closed figure keeps
that raster alive with it. Sam's Home PC (16 GB, also running his trading
desks) froze on 2026-11-21 when the mix pipeline ran out of memory; the track
preview renderer was fixed on 2026-11-23, the other renderers were not.

Measured 2026-11-23 on the Home PC (synthetic 5-minute inputs, four calls per
renderer in one process with the automatic collector switched off): every
renderer kept its figure alive after it returned, private memory grew by 3 MB
to 127 MB per call, and one gc.collect() freed 10 MB to 407 MB.

The decorator changes nothing about the picture: it only runs gc.collect()
after the wrapped function returned, and passes the return value, or the
exception, through unchanged.
"""
from __future__ import annotations

import functools
import gc


def frees_figures(fn):
    """Call fn, then collect the cyclic garbage its closed figures are in.

    The wrapper carries frees_figures = True so a test can find the renderers
    that use it.
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        finally:
            gc.collect()

    wrapper.frees_figures = True
    return wrapper
