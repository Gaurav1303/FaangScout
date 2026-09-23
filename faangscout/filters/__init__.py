"""Job filters.

Importing this package registers every built-in filter in
:data:`faangscout.filters.base.REGISTRY`. A filter activates automatically
once its key appears in ``SearchCriteria.filters`` - see ``base.Filter``.
"""

from .base import REGISTRY, Filter, FilterPipeline, register  # noqa: F401
from . import experience, location, role, semantic, time_window  # noqa: F401

__all__ = ["REGISTRY", "Filter", "FilterPipeline", "register"]
