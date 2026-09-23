"""Keep jobs located in a given country (``criteria.filters["location"]``).

Boards write locations every which way - "Pune, India", "Hyderabad, TS, IN",
"Bengaluru, Karnataka, IND", "India - Hyderabad", or a bare city like
"Bangalore" or "Noida" (Adobe). So a country matches on its name, its ISO
codes, or one of its major cities and states.

ISO codes only count as the *last* part of a location: "Hyderabad, TS, IN"
is India, but "Indianapolis, IN, US" is Indiana. A job with several locations
passes if any one of them is in the country. A job with no location at all
is dropped - its country is unknown.
"""

from __future__ import annotations

import re

from ..models import Job, Rejection, SearchCriteria
from .base import Filter, register

#: Countries with more than a name to go on. Anything else matches by name.
COUNTRIES: dict[str, dict[str, tuple[str, ...]]] = {
    "india": {
        "codes": ("in", "ind"),
        "places": (
            "bengaluru", "bangalore", "hyderabad", "secunderabad", "pune", "mumbai", "navi mumbai",
            "thane", "chennai", "gurugram", "gurgaon", "noida", "greater noida", "new delhi", "delhi",
            "kolkata", "ahmedabad", "kochi", "cochin", "thiruvananthapuram", "trivandrum", "jaipur",
            "indore", "coimbatore", "chandigarh", "mohali", "mysuru", "mysore", "vadodara", "nagpur",
            "bhubaneswar", "visakhapatnam", "vizag", "lucknow", "goa", "karnataka", "telangana",
            "maharashtra", "tamil nadu", "haryana", "uttar pradesh", "kerala", "gujarat",
            "west bengal", "andhra pradesh", "ncr",
        ),
    },
}


def _country_spec(name: str) -> dict[str, tuple[str, ...]]:
    spec = COUNTRIES.get(name.lower().strip(), {})
    return {"names": (name.lower().strip(),), "codes": spec.get("codes", ()), "places": spec.get("places", ())}


def location_matches(location: str, country: str) -> bool:
    spec = _country_spec(country)
    text = location.lower()
    words = set(re.findall(r"[a-z]+", text))
    if any(re.search(rf"\b{re.escape(n)}\b", text) for n in spec["names"] + spec["places"]):
        return True
    # ISO code as the final component: "Hyderabad, TS, IN" / "Bengaluru, KA, IND".
    last = re.split(r"[,|/-]", text)[-1].strip()
    if last in spec["codes"]:
        return True
    # "IND" never means anything else; bare "IN" only counts in final position.
    return any(code in words for code in spec["codes"] if len(code) > 2)


@register("location")
class LocationFilter(Filter):
    order = 30

    def apply(self, jobs: list[Job], criteria: SearchCriteria) -> tuple[list[Job], list[Rejection]]:
        wanted = criteria.filters.get("location")
        countries = [wanted] if isinstance(wanted, str) else list(wanted or [])
        countries = [c for c in countries if c and str(c).strip()]
        if not countries:
            return jobs, []

        kept: list[Job] = []
        rejected: list[Rejection] = []
        label = ", ".join(countries)
        for job in jobs:
            if not job.locations:
                rejected.append(Rejection(job, self.name, "no location listed"))
            elif any(location_matches(loc, c) for loc in job.locations for c in countries):
                kept.append(job)
            else:
                rejected.append(Rejection(job, self.name, f"{job.location_text!r} is not in {label}"))
        return kept, rejected
