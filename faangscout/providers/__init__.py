"""Job-board provider plugins.

Importing this package registers every built-in provider in
:data:`faangscout.providers.base.REGISTRY`. Adding a new board backend means
adding a module here with an ``@register("name")``-decorated ``Provider``
subclass and importing it below - nothing else in the codebase changes.
"""

from .base import REGISTRY, FetchHints, Provider, ProviderError, get_provider  # noqa: F401
from . import (  # noqa: F401
    amazon,
    apple,
    ashby,
    eightfold,
    greenhouse,
    jobvite,
    lever,
    oracle_hcm,
    phonepe,
    rippling_ats,
    sharechat,
    smartrecruiters,
    talentbrew,
    workday,
)

__all__ = ["REGISTRY", "Provider", "ProviderError", "get_provider", "FetchHints"]
