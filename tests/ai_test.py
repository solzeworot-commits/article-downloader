"""AI helper on fake services (v7.7): python ai_test.py <portable/scripts>

One fake server answers like the five services in the shapes of their APIs:
Perplexity (the Agent API answers, the old chat completions answer 403
"chat_completions_not_available", as a real key got in November 2026),
OpenAI, Anthropic (Messages API), GigaChat (OAuth token, then chat) and
YandexGPT (Foundation Models). For every service: a good key, a wrong key, an
empty balance and the special cases each one has.
"""
import json
import os
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

TMP = Path(tempfile.mkdtemp(prefix="ad_ai_"))
PORT = 8017
BASE = f"http://127.0.0.1:{PORT}"
os.environ.update(
    ARTICLE_AI_CHAT_URL=BASE + "/chat/completions",
    ARTICLE_AI_AGENT_URL=BASE + "/v1/responses",
    ARTICLE_AI_OPENAI_URL=BASE + "/openai",
    ARTICLE_AI_ANTHROPIC_URL=BASE + "/anthropic",
    ARTICLE_AI_GIGACHAT_OAUTH_URL=BASE + "/gigachat/oauth",
    ARTICLE_AI_GIGACHAT_URL=BASE + "/gigachat/api",
    ARTICLE_AI_YANDEX_URL=BASE + "/yandex",
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
CONCEPTS = ('{"concepts": [["grape pomace", "grape marc", "winery waste"], ["polyphenols", "phenolic compounds*"], '
            '["ultrasound-assisted extraction", "AND sonication"]], "note": "Синонимы выжимок важны."}')


def answer_for(system: str, user: str, chatter: bool = False) -> str:
    if "single word OK" in system:
        return "OK"
    if "literature search" in system:
        # Chatter, a fence and a citation mark around the JSON, as models like to do.
        return f"Here you go:\n```json\n{CONCEPTS} [1]\n```" if chatter else CONCEPTS
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

    def do_GET(self):
        FAKE["calls"].append((self.path, None, self.headers))
        if self.path == "/openai/models":
            if self.headers.get("Authorization") == "Bearer sk-bad":
                return self.send(401, {"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}})
            return self.send(200, {"object": "list", "data": [{"id": i} for i in
                                                               ("gpt-4o-mini", "gpt-5-mini", "gpt-5.2-mini", "gpt-5.2")]})
        self.send(404, {"error": "no such path"})

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"])).decode()
        body = json.loads(raw) if self.headers.get("Content-Type", "").startswith("application/json") else raw
        FAKE["calls"].append((self.path, body, self.headers))
        h = self.headers
        if self.path.startswith("/openai/"):
            return self.openai(body, h.get("Authorization", "").removeprefix("Bearer "))
        if self.path.startswith("/anthropic/"):
            return self.anthropic(body, h)
        if self.path.startswith("/gigachat/"):
            return self.gigachat(body, h)
        if self.path.startswith("/yandex/"):
            return self.yandex(body, h)
        return self.perplexity(body, h.get("Authorization", "").removeprefix("Bearer "))

    # --- Perplexity ---------------------------------------------------------
    def perplexity(self, body, key):
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
            text = answer_for(body["messages"][0]["content"], body["messages"][1]["content"], chatter=True)
            return self.send(200, {
                "id": "x", "created": 1, "model": body["model"], "object": "chat.completion",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                             "delta": {"role": "assistant", "content": ""}, "finish_reason": "stop"}],
                "search_results": [{"title": "A paper", "url": "https://example.org/p"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15,
                          "cost": {"input_tokens_cost": 0, "output_tokens_cost": 0, "total_cost": 0.0061}}})
        assert body["preset"] == ai.PERPLEXITY_PRESET and body["input"] and body["instructions"], body
        text = answer_for(body["instructions"], body["input"], chatter=True)
        return self.send(200, {
            "id": "r", "created_at": 1, "model": "xai/grok", "object": "response", "status": "completed",
            "output": [{"type": "search_results", "results": [{"id": 1, "title": "B", "url": "https://example.org/b",
                                                                "snippet": ""}]},
                       {"type": "message", "id": "m", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": text}]}],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15,
                      "cost": {"currency": "USD", "input_cost": 0, "output_cost": 0, "total_cost": 0.002}}})

    # --- OpenAI -------------------------------------------------------------
    def openai(self, body, key):
        if key == "sk-bad":
            return self.send(401, {"error": {"message": "Incorrect API key provided", "code": "invalid_api_key"}})
        if key == "sk-broke":
            return self.send(429, {"error": {"message": "You exceeded your current quota, please check your plan "
                                             "and billing details.", "type": "insufficient_quota",
                                             "code": "insufficient_quota"}})
        if key == "sk-region":
            return self.send(403, {"error": {"code": "unsupported_country_region_territory",
                                             "message": "Country, region, or territory not supported",
                                             "type": "request_forbidden"}})
        if key == "sk-noreason" and "reasoning_effort" in body:
            return self.send(400, {"error": {"message": "Unsupported parameter: 'reasoning_effort' is not supported "
                                             "with this model.", "type": "invalid_request_error"}})
        text = answer_for(body["messages"][0]["content"], body["messages"][1]["content"])
        return self.send(200, {"id": "c", "object": "chat.completion", "model": body["model"], "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text, "refusal": None}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})

    # --- Anthropic ----------------------------------------------------------
    def anthropic(self, body, h):
        key = h.get("x-api-key")
        assert h.get("anthropic-version") == ai.ANTHROPIC_VERSION
        if key == "sk-ant-bad":
            return self.send(401, {"type": "error", "error": {"type": "authentication_error",
                                                              "message": "invalid x-api-key"}})
        if key == "sk-ant-broke":
            return self.send(400, {"type": "error", "error": {
                "type": "invalid_request_error",
                "message": "Your credit balance is too low to access the Anthropic API. Please go to Plans & Billing."}})
        if key == "sk-ant-nobeta" and h.get("anthropic-beta"):
            return self.send(400, {"type": "error", "error": {
                "type": "invalid_request_error",
                "message": "Unexpected value(s) `server-side-fallback-2026-07-01` for the `anthropic-beta` header."}})
        if key == "sk-ant-refuse":
            return self.send(200, {"id": "m", "type": "message", "role": "assistant", "model": body["model"],
                                   "content": [], "stop_reason": "refusal",
                                   "stop_details": {"type": "refusal", "category": None, "explanation": ""},
                                   "usage": {"input_tokens": 10, "output_tokens": 0}})
        text = answer_for(body["system"], body["messages"][0]["content"])
        return self.send(200, {"id": "m", "type": "message", "role": "assistant", "model": body["model"],
                               "content": [{"type": "thinking", "thinking": "", "signature": "s"},
                                           {"type": "text", "text": text}],
                               "stop_reason": "end_turn", "usage": {"input_tokens": 1000, "output_tokens": 200}})

    # --- GigaChat -----------------------------------------------------------
    def gigachat(self, body, h):
        if self.path == "/gigachat/oauth":
            assert h.get("RqUID") and h.get("Content-Type") == "application/x-www-form-urlencoded"
            key = h.get("Authorization", "").removeprefix("Basic ")
            scope = parse_qs(body)["scope"][0]
            if key == "giga-bad":
                return self.send(401, {"code": 6, "message": "Credentials doesn't match db data"})
            if key == "giga-b2b" and scope != "GIGACHAT_API_B2B":
                return self.send(400, {"code": 7, "message": "scope from db not fully includes consumed scope"})
            return self.send(200, {"access_token": "tok-" + key, "expires_at": 4102444800000})
        token = h.get("Authorization", "").removeprefix("Bearer ")
        if token == "tok-giga-broke" or (token == "tok-giga-lite" and body["model"] == "GigaChat-2-Pro"):
            return self.send(402, {"status": 402, "message": "Payment Required"})
        text = answer_for(body["messages"][0]["content"], body["messages"][1]["content"])
        return self.send(200, {"choices": [{"message": {"role": "assistant", "content": text}, "index": 0,
                                            "finish_reason": "stop"}], "model": body["model"], "object": "chat.completion"})

    # --- YandexGPT ----------------------------------------------------------
    def yandex(self, body, h):
        key = h.get("Authorization", "")
        if key == "Api-Key AQV-bad":
            return self.send(401, {"error": {"grpcCode": 16, "httpCode": 401, "message": "Unknown api key",
                                             "httpStatus": "Unauthorized"}})
        if key == "Api-Key AQV-broke":
            return self.send(403, {"error": {"grpcCode": 7, "httpCode": 403, "message": "The billing account is not "
                                             "active", "httpStatus": "Forbidden"}})
        if key == "Api-Key AQV-norole":
            return self.send(403, {"error": {"grpcCode": 7, "httpCode": 403, "message": "Permission denied",
                                             "httpStatus": "Forbidden"}})
        assert h.get("x-folder-id") == "b1gfolder" and body["modelUri"].startswith("gpt://b1gfolder/"), body
        text = answer_for(body["messages"][0]["text"], body["messages"][1]["text"])
        return self.send(200, {"result": {"alternatives": [{"message": {"role": "assistant", "text": text},
                                                            "status": "ALTERNATIVE_STATUS_FINAL"}],
                                          "usage": {"inputTextTokens": "10", "completionTokens": "5",
                                                    "totalTokens": "15"}, "modelVersion": "x"}})


server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()


def paths_since(n):
    return [c[0] for c in FAKE["calls"][n:]]


def last_body():
    return FAKE["calls"][-1][1]


def kind(provider, keys):
    return ai.check_key(provider, keys)["kind"]


TOPIC = "извлечение полифенолов из виноградных выжимок ультразвуком"
LINES = ["grape pomace; grape marc; winery waste", "polyphenols; phenolic compounds",
         "ultrasound-assisted extraction; sonication"]
BATCH = [{"doi": f"10.1/{i}", "title": f"Title {i}", "abstract": "Text " * 500 if i == 2 else ""} for i in range(1, 6)]
VERDICTS = ["include", "exclude", "maybe", "include", "exclude"]


def check_jobs(provider, keys):
    res = ai.suggest_search(provider, keys, TOPIC)
    assert res["lines"] == LINES, (provider, res["lines"])
    advice, _ = ai.screen(provider, keys, BATCH, "grape pomace polyphenols")
    assert [advice[f"10.1/{i}"]["verdict"] for i in range(1, 6)] == VERDICTS, (provider, advice)
    assert advice["10.1/3"]["why"] == "причина 3"
    return res


# --- keys.json takes the new fields ------------------------------------------
ds.save_keys({"openai": "sk-good", "yandex": "AQV-good", "yandex_folder": "b1gfolder"})
assert ds.load_keys()["openai"] == "sk-good" and ds.load_keys()["yandex_folder"] == "b1gfolder"
assert ds.keys_status(ds.load_keys())["openai"].startswith("••••")
ds.save_keys({"openai": None, "yandex": None, "yandex_folder": None})
assert ds.load_keys()["openai"] == ""
assert ds._terms("a; b\nc") == ["a", "b", "c"]
q = ds.build_queries({"keywords": "\n".join(LINES), "doc_type": "all"})
assert q["scopus"] == ('TITLE-ABS-KEY(("grape pomace" OR "grape marc" OR "winery waste") AND '
                       '(polyphenols OR "phenolic compounds") AND ("ultrasound-assisted extraction" OR sonication))'), q

# --- Perplexity: Agent API first; the retired chat API is not even asked -----
P = {"perplexity": "pplx-good"}
n = len(FAKE["calls"])
assert ai.check_key("perplexity", P)["ok"]
assert paths_since(n) == ["/v1/responses"], paths_since(n)
assert kind("perplexity", {"perplexity": "pplx-bad"}) == "bad_key"
assert kind("perplexity", {"perplexity": "pplx-broke"}) == "no_money"
assert kind("perplexity", {}) == "no_key"
assert kind("perplexity", {"perplexity": "Bearer pplx-good"}) == "ok", "a pasted 'Bearer ' is removed"
res = check_jobs("perplexity", P)
assert res["note"] and res["sources"][0]["url"] == "https://example.org/b" and res["cost"] == 0.002
assert last_body()["response_format"]["json_schema"]["name"] == "screening"
assert "(no abstract)" in last_body()["input"] and len(last_body()["input"]) < 6000, "long abstracts are cut"
try:
    ai.suggest_search("perplexity", P, "x")
    raise AssertionError("an empty topic was sent")
except ai.AIError as exc:
    assert exc.kind == "bad_answer"
FAKE["no_schema"] = True                       # a server refusing response_format is asked again without it
n = len(FAKE["calls"])
assert ai.suggest_search("perplexity", P, TOPIC)["lines"] == LINES
assert ["response_format" in c[1] for c in FAKE["calls"][n:]] == [True, False]
FAKE["no_schema"], FAKE["no_lang"] = False, True
assert ai.check_key("perplexity", P)["ok"] and "language_preference" not in last_body()
FAKE["no_lang"] = False
FAKE["agent_missing"], FAKE["chat_retired"] = True, False      # no Agent API: the old chat API takes over
res = ai.suggest_search("perplexity", P, TOPIC)
assert res["sources"][0]["url"] == "https://example.org/p" and FAKE["calls"][-1][0] == "/chat/completions"
FAKE["agent_missing"], FAKE["chat_retired"] = False, True      # and back
n = len(FAKE["calls"])
assert ai.check_key("perplexity", P)["ok"]
assert paths_since(n) == ["/chat/completions", "/v1/responses"], paths_since(n)
FAKE["agent_missing"] = True                                   # both gone: a clear message, not "wrong key"
res = ai.check_key("perplexity", P)
assert res["kind"] == "other" and "404" in res["message"], res
FAKE["agent_missing"] = False

# --- OpenAI: the newest gpt-N-mini, a strict JSON schema ----------------------
O = {"openai": "sk-good"}
assert ai.check_key("openai", O)["ok"]
assert paths_since(len(FAKE["calls"]) - 2) == ["/openai/models", "/openai/chat/completions"]
check_jobs("openai", O)
sent = last_body()
assert sent["model"] == "gpt-5.2-mini" and sent["reasoning_effort"] == "low", sent
assert sent["response_format"]["json_schema"]["strict"] is True
assert sent["max_completion_tokens"] >= 2000
assert kind("openai", {"openai": "sk-bad"}) == "bad_key"
assert kind("openai", {"openai": "sk-broke"}) == "no_money"
assert kind("openai", {"openai": "sk-region"}) == "region"
assert ai.check_key("openai", {"openai": "sk-noreason"})["ok"] and "reasoning_effort" not in last_body()

# --- Anthropic: Opus 5.5 at low effort, schema, fallbacks, refusal ----------
A = {"anthropic": "sk-ant-good"}
assert ai.check_key("anthropic", A)["ok"]
res = check_jobs("anthropic", A)
sent, headers = last_body(), FAKE["calls"][-1][2]
assert sent["model"] == "claude-opus-5-5" and sent["output_config"]["effort"] == "low", sent
assert sent["output_config"]["format"]["type"] == "json_schema" and sent["fallbacks"] == "default"
assert headers.get("anthropic-beta") == ai.ANTHROPIC_BETA and "temperature" not in sent
assert abs(res["cost"] - 0.008) < 1e-9, res["cost"]           # 1000 in x $4 + 200 out x $20 per million
assert kind("anthropic", {"anthropic": "sk-ant-bad"}) == "bad_key"
assert kind("anthropic", {"anthropic": "sk-ant-broke"}) == "no_money"
assert kind("anthropic", {"anthropic": "sk-ant-refuse"}) == "other"
assert ai.check_key("anthropic", {"anthropic": "sk-ant-nobeta"})["ok"]
assert "fallbacks" not in last_body() and not FAKE["calls"][-1][2].get("anthropic-beta")

# --- GigaChat: token by the Authorization key, then chat ----------------------
G = {"gigachat": "giga-good"}
n = len(FAKE["calls"])
assert ai.check_key("gigachat", G)["ok"]
assert paths_since(n) == ["/gigachat/oauth", "/gigachat/api/chat/completions"], paths_since(n)
assert FAKE["calls"][n][1] == "scope=GIGACHAT_API_PERS"
check_jobs("gigachat", G)
assert paths_since(len(FAKE["calls"]) - 1) == ["/gigachat/api/chat/completions"], "the token is reused"
assert last_body()["model"] == "GigaChat-2-Pro"
assert kind("gigachat", {"gigachat": "giga-bad"}) == "bad_key"
assert kind("gigachat", {"gigachat": "giga-broke"}) == "no_money"
assert ai.check_key("gigachat", {"gigachat": "giga-b2b"})["ok"]          # another scope
ai._state["models"].pop("gigachat", None)
assert ai.check_key("gigachat", {"gigachat": "giga-lite"})["ok"] and last_body()["model"] == "GigaChat-2"

# --- YandexGPT: API key + folder ID -------------------------------------------
Y = {"yandex": "AQV-good", "yandex_folder": "b1gfolder"}
assert ai.check_key("yandex", Y)["ok"]
check_jobs("yandex", Y)
assert last_body()["modelUri"] == "gpt://b1gfolder/yandexgpt/latest"
assert FAKE["calls"][-1][2].get("Authorization") == "Api-Key AQV-good"
assert kind("yandex", {"yandex": "AQV-good"}) == "no_key", "no folder ID"
assert kind("yandex", {"yandex": "AQV-bad", "yandex_folder": "b1gfolder"}) == "bad_key"
assert kind("yandex", {"yandex": "AQV-broke", "yandex_folder": "b1gfolder"}) == "no_money"
res = ai.check_key("yandex", {"yandex": "AQV-norole", "yandex_folder": "b1gfolder"})
assert res["kind"] == "bad_key" and "ai.languageModels.user" in res["message"], res

assert kind("nonsense", P) == "other"
server.shutdown()
print("ai tests: OK (5 services)")
