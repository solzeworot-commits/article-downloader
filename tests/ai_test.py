"""AI helper on a fake Perplexity (v7.6.1): python ai_test.py <portable/scripts>

The fake behaves like Perplexity in November 2026: the Agent API
(/v1/responses) answers, the old Sonar chat completions answer 403
"chat_completions_not_available" (the text a real key got). Shapes follow the
official perplexityai SDK. Keys: pplx-good works, pplx-bad is rejected (401),
pplx-broke has no money (402 with a credits message).
"""
import json
import os
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="ad_ai_"))
PORT = 8017
os.environ.update(
    ARTICLE_AI_CHAT_URL=f"http://127.0.0.1:{PORT}/chat/completions",
    ARTICLE_AI_AGENT_URL=f"http://127.0.0.1:{PORT}/v1/responses",
    ARTICLE_KEYS_FILE=str(TMP / "keys.json"),
    NO_PROXY="127.0.0.1,localhost",
)
sys.path.insert(0, sys.argv[1])
import ai_helper as ai  # noqa: E402
import doi_search as ds  # noqa: E402

FAKE = {"chat_retired": True, "agent_missing": False, "no_schema": False, "no_lang": False, "calls": []}
RETIRED = {"error": {"message": "Sonar is now the Agent API. Use /v1/responses instead of /v1/sonar. Migrate here: "
                                "https://docs.perplexity.ai/docs/agent-api/migrate-from-sonar/overview",
                     "type": "chat_completions_not_available", "code": 403}}


def answer_for(system: str, user: str) -> str:
    if "single word OK" in system:
        return "OK"
    if "literature search" in system:
        # Chatter, a fence and a citation mark around the JSON, as models like to do.
        return ('Here you go:\n```json\n{"concepts": [["grape pomace", "grape marc", "winery waste"], '
                '["polyphenols", "phenolic compounds*"], ["ultrasound-assisted extraction", "AND sonication"]], '
                '"note": "Синонимы выжимок важны."} [1]\n```')
    if "pre-screen" in system:
        n = len(re.findall(r"^Paper \d+$", user, re.M))
        verdicts = ["include", "exclude", "maybe"]
        return json.dumps({"papers": [{"id": i, "verdict": verdicts[(i - 1) % 3], "why": f"причина {i}"}
                                      for i in range(1, n + 1)]}, ensure_ascii=False)
    return "?"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FAKE["calls"].append((self.path, body))
        key = self.headers.get("Authorization", "").removeprefix("Bearer ")
        if self.path == "/chat/completions" and FAKE["chat_retired"]:
            return self.send(403, RETIRED)
        if self.path == "/v1/responses" and FAKE["agent_missing"]:
            return self.send(404, {"error": {"message": "Not Found"}})
        if key == "pplx-bad":
            return self.send(401, {"error": {"message": "Invalid API key", "type": "invalid_api_key"}})
        if key == "pplx-broke":
            return self.send(402, {"error": {"message": "Insufficient credits", "type": "insufficient_quota"}})
        if "language_preference" in body and FAKE["no_lang"]:
            return self.send(400, {"error": {"message": "Unknown parameter: language_preference"}})
        if "response_format" in body and FAKE["no_schema"]:
            return self.send(400, {"error": {"message": "response_format is not supported with presets"}})
        if self.path == "/chat/completions":
            text = answer_for(body["messages"][0]["content"], body["messages"][1]["content"])
            return self.send(200, {
                "id": "x", "created": 1, "model": body["model"], "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                             "delta": {"role": "assistant", "content": ""}, "finish_reason": "stop"}],
                "search_results": [{"title": "A paper", "url": "https://example.org/p"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                          "cost": {"input_tokens_cost": 0, "output_tokens_cost": 0, "total_cost": 0.0061}}})
        if self.path == "/v1/responses":
            assert body["preset"] == ai.AGENT_PRESET and body["input"] and body["instructions"], body
            text = answer_for(body["instructions"], body["input"])
            return self.send(200, {
                "id": "r", "created_at": 1, "model": "xai/grok", "object": "response", "status": "completed",
                "output": [{"type": "search_results", "results": [{"id": 1, "title": "B", "url": "https://example.org/b",
                                                                    "snippet": ""}]},
                           {"type": "message", "id": "m", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": text}]}],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                          "cost": {"currency": "USD", "input_cost": 0, "output_cost": 0, "total_cost": 0.002}}})
        self.send(404, {"error": "no such path"})


server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()


def paths_since(n):
    return [c[0] for c in FAKE["calls"][n:]]


# --- the key, on the Agent API; the retired chat API is not even asked ------
n = len(FAKE["calls"])
res = ai.check_key("pplx-good")
assert res["ok"], res
assert paths_since(n) == ["/v1/responses"], paths_since(n)
res = ai.check_key("pplx-bad")
assert not res["ok"] and res["kind"] == "bad_key", res
res = ai.check_key("pplx-broke")
assert res["kind"] == "no_money", res
assert ai.check_key("")["kind"] == "no_key"
ds.save_keys({"perplexity": "pplx-good"})
assert ds.load_keys()["perplexity"] == "pplx-good"
assert ds.keys_status(ds.load_keys())["perplexity"].startswith("••••")
ds.save_keys({"perplexity": None})
assert ds.load_keys()["perplexity"] == ""

# --- topic in own words -> concepts -> queries ------------------------------
res = ai.suggest_search("pplx-good", "извлечение полифенолов из виноградных выжимок ультразвуком")
assert res["lines"] == ["grape pomace; grape marc; winery waste", "polyphenols; phenolic compounds",
                        "ultrasound-assisted extraction; sonication"], res["lines"]
assert res["note"] and res["sources"][0]["url"] == "https://example.org/b" and res["cost"] == 0.002
sent = FAKE["calls"][-1][1]
assert sent["response_format"]["json_schema"]["name"] == "search_concepts", sent
q = ds.build_queries({"keywords": "\n".join(res["lines"]), "doc_type": "all"})
assert q["scopus"] == ('TITLE-ABS-KEY(("grape pomace" OR "grape marc" OR "winery waste") AND '
                       '(polyphenols OR "phenolic compounds") AND ("ultrasound-assisted extraction" OR sonication))'), q
assert q["wos"].startswith('TS=(("grape pomace" OR "grape marc"'), q["wos"]
assert ds._terms("a; b\nc") == ["a", "b", "c"]
try:
    ai.suggest_search("pplx-good", "x")
    raise AssertionError("an empty topic was sent")
except ai.AIError as exc:
    assert exc.kind == "bad_answer"

# --- screening ----------------------------------------------------------------
batch = [{"doi": f"10.1/{i}", "title": f"Title {i}", "abstract": "Text " * 500 if i == 2 else ""} for i in range(1, 6)]
advice, cost = ai.screen("pplx-good", batch, "grape pomace polyphenols")
assert [advice[f"10.1/{i}"]["verdict"] for i in range(1, 6)] == ["include", "exclude", "maybe", "include", "exclude"]
assert advice["10.1/3"]["why"] == "причина 3" and cost > 0
sent = FAKE["calls"][-1][1]
assert sent["response_format"]["json_schema"]["name"] == "screening"
assert "(no abstract)" in sent["input"] and len(sent["input"]) < 6000, "long abstracts are cut"

# --- a server that refuses response_format is asked again without it -------
FAKE["no_schema"] = True
n = len(FAKE["calls"])
res = ai.suggest_search("pplx-good", "grape pomace polyphenols")
assert res["lines"][0].startswith("grape pomace"), res
assert ["response_format" in c[1] for c in FAKE["calls"][n:]] == [True, False]
n = len(FAKE["calls"])
ai.suggest_search("pplx-good", "grape pomace polyphenols")
assert len(FAKE["calls"]) == n + 1 and "response_format" not in FAKE["calls"][-1][1], "remembered"
FAKE["no_schema"] = False
FAKE["no_lang"] = True
assert ai.check_key("pplx-good")["ok"] and "language_preference" not in FAKE["calls"][-1][1]
FAKE["no_lang"] = False

# --- no Agent API (404): the old chat API takes over, and back ---------------
FAKE["agent_missing"], FAKE["chat_retired"] = True, False
res = ai.suggest_search("pplx-good", "grape pomace polyphenols")
assert res["sources"][0]["url"] == "https://example.org/p" and FAKE["calls"][-1][0] == "/chat/completions"
assert FAKE["calls"][-1][1].get("search_mode") == "academic"
assert ai.check_key("pplx-good")["ok"] and FAKE["calls"][-1][1].get("disable_search") is True
FAKE["agent_missing"], FAKE["chat_retired"] = False, True
n = len(FAKE["calls"])
res = ai.check_key("pplx-good")
assert res["ok"], res
assert paths_since(n) == ["/chat/completions", "/v1/responses"], paths_since(n)
assert ai.check_key("pplx-broke")["kind"] == "no_money"

# --- both gone: a clear message, not "wrong key" -----------------------------
FAKE["agent_missing"] = True
res = ai.check_key("pplx-good")
assert res["kind"] == "other" and "404" in res["message"], res

server.shutdown()
print("ai tests: OK")
