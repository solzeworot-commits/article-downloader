"""
Fake Scopus Search API, Web of Science Starter API and OpenAlex for tests:

    python fake_api.py            (port 8001)

Point the program at it with
    ARTICLE_SCOPUS_URL=http://127.0.0.1:8001/scopus
    ARTICLE_WOS_URL=http://127.0.0.1:8001/wos
    ARTICLE_OPENALEX_URL=http://127.0.0.1:8001/openalex
    ARTICLE_WOS_DELAY=0

Keys: "good-scopus-key-1234" / "good-wos-key-5678" work; a key starting with
"noaccess" gets Scopus' "not entitled" answer, "quota" a 429, "pending" the
WoS 403 of an unapproved subscription; anything else is an invalid key.
The response shapes copy the published examples of each API.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

SCOPUS_TOTAL, WOS_TOTAL, OPENALEX_TOTAL = 60, 40, 30


def scopus_entry(i):
    entry = {"dc:title": f"Scopus paper {i} on grape pomace", "prism:publicationName": "Food Chemistry",
             "prism:coverDate": f"{2015 + i % 10}-05-01"}
    if i % 10 != 9:  # every tenth record has no DOI
        entry["prism:doi"] = f"10.9999/common.{i}" if i < 10 else f"10.9999/scopus.{i}"
    return entry


def wos_hit(i):
    hit = {"uid": f"WOS:{i:015d}", "title": f"WoS paper {i}", "types": ["Article"],
           "source": {"sourceTitle": "JOURNAL OF FOOD SCIENCE", "publishYear": 2016 + i % 8}}
    if i % 8 != 7:
        hit["identifiers"] = {"doi": f"10.9999/COMMON.{i}" if i < 10 else f"10.9999/wos.{i}"}
    return hit


def openalex_work(i):
    return {"doi": f"https://doi.org/10.9999/openalex.{i}" if i % 6 else None,
            "display_name": f"OpenAlex work {i}", "publication_year": 2020,
            "primary_location": {"source": {"display_name": "Molecules"}}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, code, data, headers=None):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}

        if url.path == "/scopus":
            key = self.headers.get("X-ELS-APIKey", "")
            if key.startswith("quota"):
                return self.send_json(429, {"error-response": {"error-code": "TOO_MANY_REQUESTS",
                                                               "error-message": "Quota Exceeded"}})
            if key.startswith("noaccess"):
                return self.send_json(401, {"service-error": {"status": {
                    "statusCode": "AUTHORIZATION_ERROR",
                    "statusText": "Requestor configuration settings insufficient for access to this resource."}}})
            if key != "good-scopus-key-1234":
                return self.send_json(401, {"error-response": {"error-code": "APIKEY_INVALID",
                                                               "error-message": "Invalid API Key"}})
            start = 0 if q.get("cursor", "*") == "*" else int(q["cursor"][1:])
            count = int(q.get("count", 25))
            ids = range(start, min(start + count, SCOPUS_TOTAL))
            result = {"opensearch:totalResults": str(SCOPUS_TOTAL),
                      "cursor": {"@current": q.get("cursor", "*"), "@next": f"c{start + count}"},
                      "entry": [scopus_entry(i) for i in ids] or [{"@_fa": "true", "error": "Result set was empty"}]}
            return self.send_json(200, {"search-results": result}, {"X-RateLimit-Remaining": "19876"})

        if url.path == "/wos":
            key = self.headers.get("X-ApiKey", "")
            if key.startswith("quota"):
                return self.send_json(429, {"message": "API rate limit exceeded"})
            if key.startswith("pending"):
                return self.send_json(403, {"message": "You cannot consume this service"})
            if key != "good-wos-key-5678":
                return self.send_json(401, {"message": "Invalid authentication credentials"})
            page, limit = int(q.get("page", 1)), int(q.get("limit", 50))
            ids = range((page - 1) * limit, min(page * limit, WOS_TOTAL))
            return self.send_json(200, {"metadata": {"total": WOS_TOTAL, "page": page, "limit": limit},
                                        "hits": [wos_hit(i) for i in ids]},
                                  {"X-RateLimit-Remaining-Day": "48"})

        if url.path == "/openalex" and "abstract_inverted_index" in q.get("select", ""):
            # Lookup by DOI for screening: every DOI ending in ".7" is unknown.
            dois = q.get("filter", "")[len("doi:"):].split("|")
            works = []
            for doi in dois:
                if doi.endswith(".7"):
                    continue
                words = f"This study examines {doi} grape pomace polyphenols extraction yield".split()
                works.append({
                    "doi": "https://doi.org/" + doi, "display_name": f"Paper about {doi}",
                    "publication_year": 2021, "language": "en",
                    "authorships": [{"author": {"display_name": n}} for n in ("Anna Petrova", "John Smith")],
                    "biblio": {"volume": "12", "issue": "3", "first_page": "45", "last_page": "56"},
                    "primary_location": {"source": {"display_name": "Food Chemistry"}},
                    # "*.6": no abstract here (as with Elsevier); Europe PMC has it
                    "abstract_inverted_index": None if doi.endswith(".6") else {w: [i] for i, w in enumerate(words)},
                })
            return self.send_json(200, {"meta": {"count": len(works)}, "results": works})

        if url.path == "/europepmc":
            import re as _re
            dois = _re.findall(r'DOI:"([^"]+)"', q.get("query", ""))
            result = [{"doi": d, "abstractText": f"<p>Europe PMC abstract of {d} on grape pomace.</p>"}
                      for d in dois if d.endswith(".6")]
            return self.send_json(200, {"hitCount": len(result), "resultList": {"result": result}})

        if url.path == "/crossref":
            dois = [f[len("doi:"):] for f in q.get("filter", "").split(",") if f.startswith("doi:")]
            if "language" in q.get("select", ""):   # as the real Crossref answers
                return self.send_json(400, {"status": "failed", "message-type": "validation-failure",
                                            "message": [{"type": "select-not-available", "value": "language"}]})
            items = [{"DOI": d.upper(), "title": [f"Paper about {d}"], "container-title": ["Food Chemistry"],
                      "volume": "12", "issue": "3", "page": "45-56", "issued": {"date-parts": [[2021, 5]]},
                      "author": [{"family": "Petrova", "given": "Anna"}, {"family": "Smith", "given": "John A."}]}
                     for d in dois if not d.endswith(".7")]
            return self.send_json(200, {"status": "ok", "message": {"items": items}})

        if url.path == "/openalex" and "keywords" in q.get("select", ""):
            # Keyword hints: keywords and topics of the most relevant works.
            pool = ["Grape pomace", "Polyphenols", "Antioxidant activity", "Extraction",
                    "Anthocyanins", "Wine by-products", "Dietary fiber"]
            works = []
            for i in range(int(q.get("per-page", 25))):
                kws = [k for j, k in enumerate(pool) if (i + j) % (j + 2) == 0]
                topics = [{"display_name": "Phenolic compounds in plants"}] if i % 3 == 0 else []
                works.append({"keywords": [{"display_name": k, "score": 0.5} for k in kws], "topics": topics})
            return self.send_json(200, {"meta": {"count": 1234}, "results": works})

        if url.path == "/openalex":
            start = 0 if q.get("cursor", "*") == "*" else int(q["cursor"][1:])
            per_page = int(q.get("per-page", 25))
            ids = range(start, min(start + per_page, OPENALEX_TOTAL))
            nxt = f"c{start + per_page}" if start + per_page < OPENALEX_TOTAL else None
            return self.send_json(200, {"meta": {"count": OPENALEX_TOTAL, "next_cursor": nxt},
                                        "results": [openalex_work(i) for i in ids]})

        self.send_json(404, {"message": "not found"})


def start(port=8001):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8001), Handler).serve_forever()
