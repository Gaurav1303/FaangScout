"""A local stand-in for real job boards, for running FaangScout offline.

Serves Greenhouse-, Lever-, Ashby-, Amazon-, and Microsoft-shaped JSON on 127.0.0.1 so the full
pipeline (resolve -> fetch -> filter -> report) can be exercised without
reaching the real boards - useful in a sandbox with no egress, on a plane, or
in CI. Postings are generated relative to "now" so the time-window filter has
something meaningful to do.

    python demo/fixture_board.py --port 8765

Then point companies at it with demo/companies.yaml (see README "Try it
offline").
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

NOW = datetime.now(UTC)


def _iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")


# (title, hours_ago, location, department)
_POSTINGS = [
    ("Senior Backend Engineer", 2, "Remote - US", "Engineering"),
    ("Backend Engineer, Payments", 6, "San Francisco, CA", "Engineering"),
    ("Staff Software Engineer, Infrastructure", 20, "New York, NY", "Engineering"),
    ("Machine Learning Engineer", 30, "Remote - US", "ML"),
    ("Frontend Engineer", 9, "London, UK", "Engineering"),
    ("Engineering Manager, Backend", 44, "Remote - EU", "Engineering"),
    ("Product Designer", 4, "San Francisco, CA", "Design"),
    ("Technical Recruiter", 1, "Remote - US", "People"),
    ("Data Scientist", 70, "Seattle, WA", "Data"),
    ("Software Development Engineer II", 12, "Austin, TX", "Engineering"),
]


def _greenhouse_payload(board: str) -> dict:
    return {
        "jobs": [
            {
                "id": i,
                "title": title,
                "absolute_url": f"https://example-boards.test/{board}/jobs/{i}",
                "updated_at": _iso(hours),
                "location": {"name": location},
                "departments": [{"name": dept}],
                "content": f"<p>We are hiring a {title}.</p>",
            }
            for i, (title, hours, location, dept) in enumerate(_POSTINGS, start=100)
        ]
    }


def _lever_payload(site: str) -> list:
    return [
        {
            "id": f"{site}-{i}",
            "text": title,
            "hostedUrl": f"https://example-lever.test/{site}/{i}",
            "createdAt": int((NOW - timedelta(hours=hours)).timestamp() * 1000),
            "categories": {"location": location, "team": dept, "commitment": "Full-time"},
            "descriptionPlain": f"We are hiring a {title}.",
        }
        for i, (title, hours, location, dept) in enumerate(_POSTINGS, start=200)
    ]


def _ashby_payload(board: str) -> dict:
    return {
        "jobs": [
            {
                "id": f"{board}-{i}",
                "title": title,
                "jobUrl": f"https://example-ashby.test/{board}/{i}",
                "publishedAt": _iso(hours),
                "location": location,
                "isRemote": "remote" in location.lower(),
                "department": dept,
                "employmentType": "FullTime",
                "descriptionPlain": f"We are hiring a {title}.",
            }
            for i, (title, hours, location, dept) in enumerate(_POSTINGS, start=300)
        ]
    }


def _amazon_payload() -> dict:
    return {
        "error": None,
        "hits": len(_POSTINGS),
        "jobs": [
            {
                "id_icims": str(400 + i),
                "title": title,
                "job_path": f"/en/jobs/{400 + i}/{title.lower().replace(' ', '-').replace(',', '')}",
                # Amazon exposes a calendar date only, no time of day.
                "posted_date": (NOW - timedelta(hours=hours)).strftime("%B %-d, %Y"),
                "normalized_location": location,
                "job_category": dept,
                "job_schedule_type": "Full Time",
                "description": f"<p>We are hiring a {title}.</p>",
            }
            for i, (title, hours, location, dept) in enumerate(_POSTINGS)
        ],
    }


def _microsoft_payload() -> dict:
    return {
        "operationResult": {
            "result": {
                "totalJobs": len(_POSTINGS),
                "jobs": [
                    {
                        "jobId": str(500 + i),
                        "title": title,
                        "postingDate": _iso(hours),
                        "properties": {
                            "primaryLocation": location,
                            "locations": [location],
                            "workSiteFlexibility": "Up to 100% work from home"
                            if "remote" in location.lower()
                            else "Up to 50% work from home",
                            "profession": dept,
                            "employmentType": "Full-Time",
                        },
                    }
                    for i, (title, hours, location, dept) in enumerate(_POSTINGS)
                ],
            }
        }
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        path = self.path.split("?", 1)[0]
        parts = [p for p in path.split("/") if p]

        payload: object | None = None
        # Greenhouse: /v1/boards/{board}/jobs
        if len(parts) == 4 and parts[0] == "v1" and parts[1] == "boards" and parts[3] == "jobs":
            payload = _greenhouse_payload(parts[2])
        # Lever: /v0/postings/{site}
        elif len(parts) == 3 and parts[0] == "v0" and parts[1] == "postings":
            payload = _lever_payload(parts[2])
        # Ashby: /posting-api/job-board/{board}
        elif len(parts) == 3 and parts[0] == "posting-api" and parts[1] == "job-board":
            payload = _ashby_payload(parts[2])
        # Amazon: /en/search.json
        elif parts == ["en", "search.json"]:
            payload = _amazon_payload()
        # Microsoft: /search/api/v1/search
        elif parts == ["search", "api", "v1", "search"]:
            payload = _microsoft_payload()

        if payload is None:
            self.send_error(404, "no fixture for this path")
            return

        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        print(f"[fixture-board] {fmt % args}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[fixture-board] serving Greenhouse/Lever/Ashby fixtures on http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
