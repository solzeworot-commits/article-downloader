# -*- coding: utf-8 -*-
# © 2026 Bucaneiro. Бесплатная программа; условия — в файле ЛИЦЕНЗИЯ.txt.
"""
AI helper on the user's own API key (v7.6; a choice of five services since 7.7).

Each user picks a service, gets an API key there, tops up its balance and
pastes the key into the program; keys live in keys.json with the other keys
and go only to that service. The author's keys are never used.

Services (PROVIDERS):
  perplexity - Agent API POST /v1/responses (preset "fast-search", searches the
               web); the retired Sonar chat completions are a fallback only.
               Perplexity answered 403 "chat_completions_not_available" to the
               old endpoint in November 2026. Shapes: perplexityai SDK 0.42.
  openai     - POST /v1/chat/completions; the model is the newest "gpt-N-mini"
               that GET /v1/models lists (free; it also checks the key).
  anthropic  - POST /v1/messages, API version 2026-10-21, model claude-opus-5-5
               at effort "low", JSON schema in output_config.format, server-side
               refusal fallbacks ("fallbacks": "default", beta
               server-side-fallback-2026-07-01). Prices for the cost line:
               $4 / $20 per million tokens in / out.
  gigachat   - Sber: the "Authorization key" buys a 30-minute token at
               POST ngw.devices.sberbank.ru:9443/api/v2/oauth (scope
               GIGACHAT_API_PERS, then B2B and CORP), then
               POST /api/v1/chat/completions. Sber's certificate is issued by
               the Russian Ministry of Digital Development: without it in the
               Windows store the program says how to install it.
  yandex     - Yandex Cloud Foundation Models POST /foundationModels/v1/completion
               with an API key of a service account and the folder ID.

Two jobs, the same for every service:
  suggest_search(provider, keys, topic) - a topic in the user's own words ->
      English search concepts with synonyms, one concept per line ("a; b; c"),
      which doi_search turns into Scopus, Web of Science and OpenAlex queries;
  screen(provider, keys, articles, topic) - a verdict per abstract (include /
      maybe / exclude) with a short reason. Only advice: the user decides.

Optional request parameters a server rejects with 400/422 are dropped and the
request is repeated; the program remembers that until it is restarted.
ARTICLE_AI_*_URL variables point the services at a fake server in the tests.
"""
from __future__ import annotations

import json
import os
import re
import ssl
import time
import urllib.error
import urllib.request
import uuid

from i18n import tr

USER_AGENT = "ArticleDownloader (+https://github.com/solzeworot-commits/article-downloader)"
SCREEN_BATCH = 10
ABSTRACT_CHARS = 1600
VERDICTS = ("include", "maybe", "exclude")

PROVIDERS = {
    "perplexity": "Perplexity",
    "openai": "ChatGPT",
    "anthropic": "Claude",
    "gigachat": "GigaChat",
    "yandex": "YandexGPT",
}
# keys.json fields of every service (the first one is the key itself).
KEY_FIELDS = {
    "perplexity": ("perplexity",),
    "openai": ("openai",),
    "anthropic": ("anthropic",),
    "gigachat": ("gigachat",),
    "yandex": ("yandex", "yandex_folder"),
}

URLS = {
    "perplexity_agent": ("ARTICLE_AI_AGENT_URL", "https://api.perplexity.ai/v1/responses"),
    "perplexity_chat": ("ARTICLE_AI_CHAT_URL", "https://api.perplexity.ai/chat/completions"),
    "openai": ("ARTICLE_AI_OPENAI_URL", "https://api.openai.com/v1"),
    "anthropic": ("ARTICLE_AI_ANTHROPIC_URL", "https://api.anthropic.com/v1"),
    "gigachat_oauth": ("ARTICLE_AI_GIGACHAT_OAUTH_URL", "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"),
    "gigachat": ("ARTICLE_AI_GIGACHAT_URL", "https://gigachat.devices.sberbank.ru/api/v1"),
    "yandex": ("ARTICLE_AI_YANDEX_URL", "https://llm.api.cloud.yandex.net/foundationModels/v1"),
}

PERPLEXITY_PRESET = "fast-search"
PERPLEXITY_CHAT_MODEL = "sonar"
ANTHROPIC_VERSION = "2026-10-21"
ANTHROPIC_BETA = "server-side-fallback-2026-07-01"
ANTHROPIC_MODELS = ("claude-opus-5-5", "claude-sonnet-5-5")
ANTHROPIC_PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0),
                    "claude-opus-4-8": (5.0, 25.0), "claude-haiku-5-5": (0.1, 0.5)}  # $ per 1M tokens
OPENAI_DEFAULT_MODEL = "gpt-5-mini"
GIGACHAT_SCOPES = ("GIGACHAT_API_PERS", "GIGACHAT_API_B2B", "GIGACHAT_API_CORP")
GIGACHAT_MODELS = ("GigaChat-2-Pro", "GigaChat-2", "GigaChat")
YANDEX_MODELS = ("yandexgpt/latest", "yandexgpt-lite/latest")

# Words of an old Perplexity endpoint answer that send people to the Agent API.
GONE_MARKERS = ("chat_completions_not_available", "/v1/responses", "agent api", "migrate-from-sonar")
REGION_MARKERS = ("unsupported_country", "country, region", "your region", "your country", "region is not supported")
MONEY_MARKERS = ("credit", "balance", "insufficient", "billing", "payment", "quota")
OPTIONAL = {
    "perplexity": ("response_format", "language_preference", "max_output_tokens", "max_tokens", "search_mode"),
    "openai": ("response_format", "reasoning_effort", "max_completion_tokens"),
}

# What the program learnt while running: endpoints, models, refused parameters, tokens.
_state: dict = {"perplexity": "agent", "refused": {}, "models": {}, "gigachat": {}}


class AIError(Exception):
    """kind: no_key, bad_key, no_money, region, cert, quota, network, bad_answer, other.
    The message is for the user."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _url(name: str) -> str:
    env, default = URLS[name]
    return os.environ.get(env) or default


def _request(provider: str, method: str, url: str, headers: dict, body: dict | None = None,
             form: str | None = None, timeout: float = 120) -> tuple[int, dict | None, str]:
    data = json.dumps(body).encode("utf-8") if body is not None else (form.encode("utf-8") if form else None)
    hdrs = {"Accept": "application/json", "User-Agent": USER_AGENT, **headers}
    if body is not None:
        hdrs.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read(4000).decode("utf-8", "replace")
        except Exception:
            text = ""
        status = exc.code
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason):
            if provider == "gigachat":
                raise AIError("cert", tr("Компьютер не доверяет сертификату Сбера. Установите сертификаты Минцифры "
                                         "(кнопка «Сертификаты Минцифры» в инструкции GigaChat) и перезапустите "
                                         "программу.")) from None
        raise AIError("network", tr("Нет связи с {service} ({reason}). Проверьте интернет.",
                                    service=PROVIDERS[provider], reason=reason)) from None
    try:
        parsed = json.loads(text) if text else None
    except ValueError:
        parsed = None
    return status, parsed if isinstance(parsed, dict) else None, text


def _error_text(data: dict | None, text: str) -> str:
    err = (data or {}).get("error")
    if isinstance(err, dict):
        detail = " ".join(str(err.get(k) or "") for k in ("message", "type", "code"))
    elif err:
        detail = str(err)
    else:
        detail = str((data or {}).get("detail") or (data or {}).get("message") or text)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", detail)).strip()[:300]


def _http_error(provider: str, status: int, data: dict | None, text: str) -> AIError:
    name = PROVIDERS[provider]
    detail = _error_text(data, text)
    low = detail.lower()
    if status in (400, 403) and any(m in low for m in REGION_MARKERS):
        return AIError("region", tr("{service} не работает в вашей стране. Выберите другого помощника: из России "
                                    "работают Perplexity, GigaChat и YandexGPT.", service=name))
    if status == 429 and provider == "yandex":
        return AIError("quota", tr("Слишком много запросов к {service} подряд. Подождите минуту и повторите.", service=name))
    if status == 402 or any(m in low for m in MONEY_MARKERS):
        return AIError("no_money", tr("На счёте {service} нет денег. Пополните баланс API — шаг «пополните счёт» "
                                      "в инструкции.", service=name))
    if status == 401:
        return AIError("bad_key", tr("Ключ {service} неверный или устарел — скопируйте его ещё раз, целиком.",
                                     service=name))
    if status == 403 and provider == "yandex":
        return AIError("bad_key", tr("Yandex Cloud не пускает: проверьте ID каталога и роль ai.languageModels.user "
                                     "у сервисного аккаунта. {detail}", detail=detail).strip())
    if status == 403:
        return AIError("bad_key", tr("{service} не принял ключ (код 403). {detail}", service=name, detail=detail).strip())
    if status == 429:
        return AIError("quota", tr("Слишком много запросов к {service} подряд. Подождите минуту и повторите.", service=name))
    if status >= 500:
        return AIError("other", tr("{service} временно не отвечает (код {code}). Попробуйте позже.",
                                   service=name, code=status))
    return AIError("other", tr("{service} ответил ошибкой {code}. {detail}", service=name, code=status,
                               detail=detail).strip())


def _send(provider: str, url: str, headers: dict, body: dict, timeout: float) -> tuple[int, dict | None, str]:
    """POST, dropping the optional parameters the server complains about (remembered)."""
    refused = _state["refused"].setdefault(provider, set())
    body = {k: v for k, v in body.items() if k not in refused}
    status, data, raw = _request(provider, "POST", url, headers, body, timeout=timeout)
    for _ in range(3):
        if status not in (400, 422):
            break
        detail = _error_text(data, raw).lower()
        names = [k for k in OPTIONAL.get(provider, ()) if k in body and k in detail]
        if "response_format" in body and "response_format" not in names and ("json_schema" in detail or "schema" in detail):
            names.append("response_format")
        if not names:
            break
        for name in names:
            refused.add(name)
            body.pop(name, None)
        status, data, raw = _request(provider, "POST", url, headers, body, timeout=timeout)
    return status, data, raw


def _ok(provider: str, status: int, data: dict | None, raw: str) -> dict:
    if status >= 400:
        raise _http_error(provider, status, data, raw)
    if data is None:
        raise AIError("bad_answer", tr("{service} прислал непонятный ответ.", service=PROVIDERS[provider]))
    return data


def _openai_like_text(data: dict) -> str:
    choices = data.get("choices") or []
    message = (choices[0] or {}).get("message") if choices else None
    return str((message or {}).get("content") or "")


# ---------------------------------------------------------------------------
# the five services
# ---------------------------------------------------------------------------

def _ask_perplexity(keys: dict, instructions: str, text: str, search: bool, max_tokens: int, lang: str,
                    timeout: float, schema) -> dict:
    headers = {"Authorization": f"Bearer {keys['perplexity']}"}
    fmt = {"type": "json_schema", "json_schema": {"name": schema[0], "schema": schema[1]}} if schema else None
    agent = {"preset": PERPLEXITY_PRESET, "instructions": instructions, "input": text,
             "max_output_tokens": max_tokens, "language_preference": lang}
    chat = {"model": PERPLEXITY_CHAT_MODEL, "max_tokens": max_tokens, "language_preference": lang,
            "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": text}]}
    chat["search_mode" if search else "disable_search"] = "academic" if search else True
    order = ["agent", "chat"] if _state["perplexity"] == "agent" else ["chat", "agent"]
    for name in order:
        url, body = (_url("perplexity_agent"), dict(agent)) if name == "agent" else (_url("perplexity_chat"), dict(chat))
        if fmt:
            body["response_format"] = fmt
        status, data, raw = _send("perplexity", url, headers, body, timeout)
        # The endpoint is gone (404), or the old one sends people to the Agent API: try the other one.
        if status in (404, 405, 410) or (name == "chat" and status in (400, 403)
                                         and any(m in _error_text(data, raw).lower() for m in GONE_MARKERS)):
            continue
        data = _ok("perplexity", status, data, raw)
        _state["perplexity"] = name
        if name == "chat":
            sources = [{"title": str(r.get("title") or ""), "url": str(r.get("url") or "")}
                       for r in data.get("search_results") or [] if isinstance(r, dict)]
            cost = (((data.get("usage") or {}).get("cost")) or {}).get("total_cost")
            return {"text": _openai_like_text(data), "sources": sources, "cost": cost}
        texts, sources = [], []
        for item in data.get("output") or []:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                texts += [str(p.get("text") or "") for p in item.get("content") or []
                          if isinstance(p, dict) and p.get("type") == "output_text"]
            elif item.get("type") == "search_results":
                sources += [{"title": str(r.get("title") or ""), "url": str(r.get("url") or "")}
                            for r in item.get("results") or [] if isinstance(r, dict)]
        if data.get("status") == "failed" and not texts:
            err = data.get("error") or {}
            raise AIError("other", tr("{service} не смог ответить: {detail}", service="Perplexity",
                                      detail=str(err.get("message") or "?")[:200]))
        cost = (((data.get("usage") or {}).get("cost")) or {}).get("total_cost")
        return {"text": "".join(texts), "sources": sources, "cost": cost}
    raise AIError("other", tr("Perplexity не нашёл ни один из своих адресов API (код 404). Обновите программу."))


def _openai_model(key: str, timeout: float) -> str:
    """The newest "gpt-N-mini" the key may use; GET /models is free and checks the key."""
    cached = _state["models"].get(("openai", key))
    if cached:
        return cached
    status, data, raw = _request("openai", "GET", _url("openai") + "/models", {"Authorization": f"Bearer {key}"},
                                 timeout=min(timeout, 60))
    if status == 401 or (status in (400, 403) and any(m in _error_text(data, raw).lower() for m in REGION_MARKERS)):
        raise _http_error("openai", status, data, raw)
    ids = [str(m.get("id") or "") for m in (data or {}).get("data") or [] if isinstance(m, dict)] if status < 400 else []
    minis = [i for i in ids if re.fullmatch(r"gpt-\d+(?:\.\d+)?-mini", i)]
    if minis:
        model = max(minis, key=lambda i: tuple(int(p) for p in re.findall(r"\d+", i)))
    else:
        model = next((m for m in ("gpt-4.1-mini", "gpt-4o-mini") if m in ids), OPENAI_DEFAULT_MODEL)
    _state["models"][("openai", key)] = model
    return model


def _ask_openai(keys: dict, instructions: str, text: str, search: bool, max_tokens: int, lang: str,
                timeout: float, schema) -> dict:
    key = keys["openai"]
    body = {"model": _openai_model(key, timeout),
            "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": text}],
            # Reasoning models count their thinking here too: room for it.
            "max_completion_tokens": max(4 * max_tokens, 2000), "reasoning_effort": "low"}
    if schema:
        body["response_format"] = {"type": "json_schema",
                                   "json_schema": {"name": schema[0], "schema": schema[1], "strict": True}}
    status, data, raw = _send("openai", _url("openai") + "/chat/completions", {"Authorization": f"Bearer {key}"},
                              body, timeout)
    data = _ok("openai", status, data, raw)
    choices = data.get("choices") or [{}]
    refusal = ((choices[0] or {}).get("message") or {}).get("refusal")
    if refusal:
        raise AIError("other", tr("{service} отказался отвечать: {detail}", service="ChatGPT", detail=str(refusal)[:200]))
    return {"text": _openai_like_text(data), "sources": [], "cost": None}


def _ask_anthropic(keys: dict, instructions: str, text: str, search: bool, max_tokens: int, lang: str,
                   timeout: float, schema) -> dict:
    url = _url("anthropic") + "/messages"
    headers = {"x-api-key": keys["anthropic"], "anthropic-version": ANTHROPIC_VERSION}
    refused = _state["refused"].setdefault("anthropic", set())
    for model in ANTHROPIC_MODELS:
        body = {"model": model, "max_tokens": 16000, "system": instructions,
                "messages": [{"role": "user", "content": text}], "output_config": {"effort": "low"}}
        if schema and "format" not in refused:
            body["output_config"]["format"] = {"type": "json_schema", "schema": schema[1]}
        if "fallbacks" not in refused:
            body["fallbacks"] = "default"
        for _ in range(3):
            hdrs = dict(headers, **({"anthropic-beta": ANTHROPIC_BETA} if "fallbacks" in body else {}))
            status, data, raw = _request("anthropic", "POST", url, hdrs, body, timeout=max(timeout, 300))
            detail = _error_text(data, raw).lower()
            if status != 400:
                break
            if "fallbacks" in body and ("anthropic-beta" in detail or "fallback" in detail):
                refused.add("fallbacks")
                body.pop("fallbacks")
            elif "format" in body["output_config"] and ("output_config" in detail or "schema" in detail):
                refused.add("format")
                body["output_config"].pop("format")
            else:
                break
        if status == 404 and "model" in _error_text(data, raw).lower():
            continue
        data = _ok("anthropic", status, data, raw)
        if data.get("stop_reason") == "refusal":
            raise AIError("other", tr("{service} отказался отвечать на этот запрос.", service="Claude"))
        answer = "".join(str(b.get("text") or "") for b in data.get("content") or []
                         if isinstance(b, dict) and b.get("type") == "text")
        usage = data.get("usage") or {}
        price = ANTHROPIC_PRICES.get(str(data.get("model") or model))
        cost = None
        if price:
            cost = (int(usage.get("input_tokens") or 0) * price[0] + int(usage.get("output_tokens") or 0) * price[1]) / 1e6
        return {"text": answer, "sources": [], "cost": cost}
    raise _http_error("anthropic", status, data, raw)


def _gigachat_token(key: str, timeout: float) -> str:
    """A 30-minute access token for the Authorization key; the scope that worked is remembered."""
    cached = _state["gigachat"].get(key)
    if cached and cached[1] - 60 > time.time():
        return cached[0]
    scopes = [s for s in (_state["gigachat"].get("scope"),) if s] + [s for s in GIGACHAT_SCOPES]
    status, data, raw = 0, None, ""
    for scope in dict.fromkeys(scopes):
        status, data, raw = _request("gigachat", "POST", _url("gigachat_oauth"), {
            "Authorization": f"Basic {key}", "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded"}, form=f"scope={scope}", timeout=min(timeout, 60))
        if status == 200 and data and data.get("access_token"):
            expires = float(data.get("expires_at") or 0)
            expires = expires / 1000 if expires > 1e11 else (expires or time.time() + 1500)
            _state["gigachat"][key] = (str(data["access_token"]), expires)
            _state["gigachat"]["scope"] = scope
            return str(data["access_token"])
        if not (status in (400, 401, 403) and "scope" in raw.lower()):
            break
    raise _http_error("gigachat", status, data, raw)


def _ask_gigachat(keys: dict, instructions: str, text: str, search: bool, max_tokens: int, lang: str,
                  timeout: float, schema) -> dict:
    token = _gigachat_token(keys["gigachat"], timeout)
    url = _url("gigachat") + "/chat/completions"
    models = [m for m in (_state["models"].get("gigachat"),) if m] + list(GIGACHAT_MODELS)
    status, data, raw = 0, None, ""
    for model in dict.fromkeys(models):
        body = {"model": model, "max_tokens": max(2 * max_tokens, 1000),
                "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": text}]}
        status, data, raw = _request("gigachat", "POST", url, {"Authorization": f"Bearer {token}"}, body, timeout=timeout)
        # No paid tokens for this model, or no such model: the next, simpler one.
        if status == 402 or (status in (400, 404) and "model" in _error_text(data, raw).lower()):
            continue
        data = _ok("gigachat", status, data, raw)
        _state["models"]["gigachat"] = model
        return {"text": _openai_like_text(data), "sources": [], "cost": None}
    raise _http_error("gigachat", status, data, raw)


def _ask_yandex(keys: dict, instructions: str, text: str, search: bool, max_tokens: int, lang: str,
                timeout: float, schema) -> dict:
    folder = keys.get("yandex_folder", "")
    if not folder:
        raise AIError("no_key", tr("Для YandexGPT нужен ещё ID каталога (folder ID) — второе поле под ключом."))
    headers = {"Authorization": f"Api-Key {keys['yandex']}", "x-folder-id": folder}
    models = [m for m in (_state["models"].get("yandex"),) if m] + list(YANDEX_MODELS)
    status, data, raw = 0, None, ""
    for model in dict.fromkeys(models):
        body = {"modelUri": f"gpt://{folder}/{model}",
                "completionOptions": {"stream": False, "temperature": 0.2, "maxTokens": str(max(2 * max_tokens, 1000))},
                "messages": [{"role": "system", "text": instructions}, {"role": "user", "text": text}]}
        status, data, raw = _request("yandex", "POST", _url("yandex") + "/completion", headers, body, timeout=timeout)
        if status in (400, 404) and "model" in _error_text(data, raw).lower():
            continue
        data = _ok("yandex", status, data, raw)
        _state["models"]["yandex"] = model
        alternatives = (data.get("result") or {}).get("alternatives") or [{}]
        answer = str(((alternatives[0] or {}).get("message") or {}).get("text") or "")
        return {"text": answer, "sources": [], "cost": None}
    raise _http_error("yandex", status, data, raw)


ASK = {"perplexity": _ask_perplexity, "openai": _ask_openai, "anthropic": _ask_anthropic,
       "gigachat": _ask_gigachat, "yandex": _ask_yandex}


def _clean(value) -> str:
    """A pasted key without spaces and the scheme words people copy along with it."""
    value = re.sub(r"\s+", "", str(value or ""))
    return re.sub(r"^(?:Bearer|Basic|Api-Key)(?=[A-Za-z0-9])", "", value, flags=re.I)


def ask(provider: str, keys: dict, instructions: str, text: str, *, search: bool = True, max_tokens: int = 1200,
        lang: str = "ru", timeout: float = 120, schema: tuple[str, dict] | None = None) -> dict:
    """One question. {"text", "sources": [{"title", "url"}], "cost": dollars or None}.
    schema: (name, JSON schema) of the answer for services that take one."""
    if provider not in PROVIDERS:
        raise AIError("other", tr("Неизвестный ИИ-помощник."))
    keys = {k: _clean(v) if k != "yandex_folder" else str(v or "").strip() for k, v in (keys or {}).items()}
    if not keys.get(provider):
        raise AIError("no_key", tr("ИИ-помощник не подключён: вставьте ключ (кнопка «🤖 ИИ-помощник» на главной)."))
    return ASK[provider](keys, instructions, text, search, max_tokens, lang, timeout, schema)


def _json_part(text: str):
    """The JSON object or array in an answer, without ```json fences, chatter or [1] marks."""
    text = re.sub(r"```(?:json)?", "", text or "").strip()
    for candidate in (text, re.sub(r"\s*\[\d+(?:\s*,\s*\d+)*\]", "", text)):
        for opener, closer in (("{", "}"), ("[", "]")):
            start, end = candidate.find(opener), candidate.rfind(closer)
            if start != -1 and end > start:
                try:
                    return json.loads(candidate[start:end + 1])
                except ValueError:
                    continue
    raise AIError("bad_answer", tr("ИИ ответил не по форме. Нажмите кнопку ещё раз."))


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------

def check_key(provider: str, keys: dict) -> dict:
    """A tiny request (a fraction of a cent): the key, the balance and the region at once."""
    try:
        answer = ask(provider, keys, "Reply with the single word OK. Do not search the web.", "ping", search=False,
                     max_tokens=32, timeout=60)
    except AIError as exc:
        return {"ok": False, "kind": exc.kind, "message": exc.message}
    return {"ok": True, "kind": "ok", "message": tr("Ключ работает ✓ ({service} отвечает)", service=PROVIDERS[provider]),
            "cost": answer.get("cost")}


SEARCH_SCHEMA = ("search_concepts", {
    "type": "object",
    "properties": {
        "concepts": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
        "note": {"type": "string"},
    },
    "required": ["concepts", "note"],
    "additionalProperties": False,
})

SCREEN_SCHEMA = ("screening", {
    "type": "object",
    "properties": {
        "papers": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "verdict": {"type": "string", "enum": list(VERDICTS)},
                "why": {"type": "string"},
            },
            "required": ["id", "verdict", "why"],
            "additionalProperties": False,
        }},
    },
    "required": ["papers"],
    "additionalProperties": False,
})

SEARCH_INSTRUCTIONS = """You help a researcher build a literature search in Scopus, Web of Science and OpenAlex.
The researcher describes the topic in their own words, often in Russian. Think of how real papers on this topic
are titled and indexed, then reply with JSON only, no other text:
{"concepts": [["main term", "synonym", "variant"], ["second concept", "synonym"]], "note": "..."}
Rules:
- 2 to 4 concepts; every concept is a list of 1 to 6 English search terms that mean the same thing;
  all concepts must be present in a relevant paper (they will be joined with AND, synonyms with OR);
- terms are short phrases used in titles and abstracts, no quotes, no wildcards, no boolean operators;
- put the most important concept first; leave out generic words like "study", "analysis", "effect";
- "note": one or two short sentences in {lang_name} that explain the choice or warn what may be missed."""


def suggest_search(provider: str, keys: dict, topic: str, lang: str = "ru") -> dict:
    """{"lines": ["a; b", "c; d"], "note", "sources", "cost"}."""
    topic = re.sub(r"\s+", " ", str(topic or "")).strip()
    if len(topic) < 3:
        raise AIError("bad_answer", tr("Опишите тему хотя бы парой слов."))
    lang_name = "Russian" if lang == "ru" else "English"
    answer = ask(provider, keys, SEARCH_INSTRUCTIONS.replace("{lang_name}", lang_name), topic[:1500], search=True,
                 max_tokens=700, lang=lang, schema=SEARCH_SCHEMA)
    data = _json_part(answer["text"])
    concepts = data.get("concepts") if isinstance(data, dict) else data
    lines = []
    for group in concepts or []:
        terms = group if isinstance(group, list) else [group]
        seen, clean = set(), []
        for term in terms:
            term = re.sub(r'[\s"*;()]+', " ", str(term)).strip(" ,.")
            term = re.sub(r"\b(AND|OR|NOT)\b", " ", term).strip()
            term = re.sub(r"\s+", " ", term)
            if term and term.lower() not in seen and len(term) <= 80:
                seen.add(term.lower())
                clean.append(term)
        if clean:
            lines.append("; ".join(clean[:6]))
    if not lines:
        raise AIError("bad_answer", tr("ИИ не предложил ни одного слова. Опишите тему подробнее."))
    note = str(data.get("note") or "") if isinstance(data, dict) else ""
    return {"lines": lines[:4], "note": note[:400], "sources": answer["sources"][:6], "cost": answer["cost"]}


TRANSLATE_SCHEMA = ("translation", {
    "type": "object",
    "properties": {"title": {"type": "string"}, "abstract": {"type": "string"}},
    "required": ["title", "abstract"],
    "additionalProperties": False,
})

TRANSLATE_INSTRUCTIONS = """You translate the title and abstract of a scientific paper into {lang_name} for a researcher.
Translate accurately and completely, in the style of a scientific text; keep numbers, units, chemical names,
abbreviations and Latin species names as they are. Do not search the web, do not add comments.
Reply with JSON only: {"title": "...", "abstract": "..."}"""


def translate(provider: str, keys: dict, title: str, abstract: str, lang: str = "ru") -> dict:
    """{"title", "abstract", "cost"} in the language of the window (Russian or English)."""
    abstract = re.sub(r"\s+", " ", str(abstract or "")).strip()
    if not abstract:
        raise AIError("bad_answer", tr("У этой статьи нет аннотации — переводить нечего."))
    lang_name = "Russian" if lang == "ru" else "English"
    text = f"Title: {str(title or '').strip()[:500]}\n\nAbstract: {abstract[:6000]}"
    answer = ask(provider, keys, TRANSLATE_INSTRUCTIONS.replace("{lang_name}", lang_name), text, search=False,
                 max_tokens=min(4000, 300 + len(abstract) // 2), lang=lang, schema=TRANSLATE_SCHEMA)
    data = _json_part(answer["text"])
    if not isinstance(data, dict) or not str(data.get("abstract") or "").strip():
        raise AIError("bad_answer", tr("ИИ ответил не по форме. Нажмите кнопку ещё раз."))
    return {"title": str(data.get("title") or "").strip(), "abstract": str(data["abstract"]).strip(),
            "cost": answer["cost"]}


TABLE_SCHEMA = ("table_row", {
    "type": "object",
    "properties": {"cells": {"type": "array", "items": {"type": "string"}}},
    "required": ["cells"],
    "additionalProperties": False,
})

TABLE_INSTRUCTIONS = """You help a researcher build a summary table of papers for a literature review.
Read the paper text given (it may be the full text or only the abstract) and fill these columns:
{columns}
Reply with JSON only: {"cells": ["...", "..."]} with exactly {n} strings, one per column, in this order.
- Write in {lang_name}, briefly (a phrase, at most 30 words per cell), keep numbers, units and names exactly;
- take facts only from the text given, do not search the web and do not guess;
- if the text does not report something, write "—"."""


def extract_table(provider: str, keys: dict, columns: list[str], title: str, text: str, lang: str = "ru") -> tuple[list, float]:
    """The cells of one article's row (one per column) and the cost."""
    columns = [str(c).strip() for c in columns if str(c).strip()][:12]
    text = str(text or "").strip()
    if not columns:
        raise AIError("bad_answer", tr("Напишите хотя бы один столбец таблицы."))
    if not text:
        raise AIError("bad_answer", tr("Нет ни текста статьи, ни аннотации."))
    lang_name = "Russian" if lang == "ru" else "English"
    listing = "\n".join(f"{i}. {c}" for i, c in enumerate(columns, 1))
    instructions = (TABLE_INSTRUCTIONS.replace("{columns}", listing).replace("{n}", str(len(columns)))
                    .replace("{lang_name}", lang_name))
    answer = ask(provider, keys, instructions, f"Title: {str(title or '').strip()[:500]}\n\n{text}", search=False,
                 max_tokens=150 + 120 * len(columns), lang=lang, timeout=240, schema=TABLE_SCHEMA)
    data = _json_part(answer["text"])
    cells = data.get("cells") if isinstance(data, dict) else data
    if not isinstance(cells, list) or not cells:
        raise AIError("bad_answer", tr("ИИ ответил не по форме. Нажмите кнопку ещё раз."))
    cells = [re.sub(r"\s+", " ", str(c)).strip()[:600] for c in cells][:len(columns)]
    cells += ["—"] * (len(columns) - len(cells))
    return cells, float(answer["cost"] or 0)


OUTLINE_SCHEMA = ("review_outline", {
    "type": "object",
    "properties": {
        "sections": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "about": {"type": "string"},
                           "papers": {"type": "array", "items": {"type": "integer"}}},
            "required": ["title", "about", "papers"],
            "additionalProperties": False}},
        "gaps": {"type": "string"},
    },
    "required": ["sections", "gaps"],
    "additionalProperties": False,
})

OUTLINE_INSTRUCTIONS = """You help a researcher plan the text of a literature review.
Below are the papers selected for the review: a number, the title, the year and the abstract or a summary.
Propose the structure of the main body of the review (no Introduction and no Conclusions): 4 to 8 sections
in a logical order, each with
- "title": a short section heading in {lang_name};
- "about": one or two sentences in {lang_name} on what the section discusses or compares, based on these papers;
- "papers": the numbers of the papers to discuss in it. Put every paper in at least one section.
Also "gaps": one to three sentences in {lang_name} on what these papers do not cover (research gaps).
Use only the papers given; do not search the web and do not invent papers. Reply with JSON only:
{"sections": [{"title": "...", "about": "...", "papers": [1, 4]}], "gaps": "..."}"""
OUTLINE_MAX = 200


def outline(provider: str, keys: dict, topic: str, papers: list[dict], lang: str = "ru") -> tuple[dict, float]:
    """papers: [{"title", "year", "abstract", "summary"}] -> ({"sections": [{"title", "about", "papers": [i]}],
    "gaps"}, cost); the paper numbers are indexes into the given list."""
    papers = papers[:OUTLINE_MAX]
    if len(papers) < 2:
        raise AIError("bad_answer", tr("Для плана нужно хотя бы две статьи."))
    per = 700 if len(papers) <= 40 else 400 if len(papers) <= 80 else 220 if len(papers) <= 140 else 0
    lang_name = "Russian" if lang == "ru" else "English"
    parts = [f"What the review is about: {str(topic or '').strip()[:1500] or '(see the papers)'}", ""]
    for i, paper in enumerate(papers, 1):
        line = f"Paper {i}: {str(paper.get('title') or '').strip()[:300]} ({paper.get('year') or 'n.d.'})"
        summary = re.sub(r"\s+", " ", str(paper.get("summary") or "")).strip()
        abstract = re.sub(r"\s+", " ", str(paper.get("abstract") or "")).strip()
        if per and (summary or abstract):
            line += "\n" + (summary or abstract)[:per]
        parts.append(line)
    answer = ask(provider, keys, OUTLINE_INSTRUCTIONS.replace("{lang_name}", lang_name), "\n".join(parts),
                 search=False, max_tokens=min(6000, 700 + 25 * len(papers)), lang=lang, timeout=300,
                 schema=OUTLINE_SCHEMA)
    data = _json_part(answer["text"])
    sections = []
    for sec in (data.get("sections") if isinstance(data, dict) else None) or []:
        if not isinstance(sec, dict) or not str(sec.get("title") or "").strip():
            continue
        nums = []
        for n in sec.get("papers") or []:
            try:
                n = int(n)
            except (TypeError, ValueError):
                continue
            if 1 <= n <= len(papers) and n - 1 not in nums:
                nums.append(n - 1)
        sections.append({"title": re.sub(r"\s+", " ", str(sec["title"])).strip()[:200],
                         "about": re.sub(r"\s+", " ", str(sec.get("about") or "")).strip()[:800],
                         "papers": nums})
    if not sections:
        raise AIError("bad_answer", tr("ИИ ответил не по форме. Нажмите кнопку ещё раз."))
    gaps = re.sub(r"\s+", " ", str(data.get("gaps") or "")).strip()[:1200]
    return {"sections": sections, "gaps": gaps}, float(answer["cost"] or 0)


SCREEN_INSTRUCTIONS = """You help a researcher pre-screen papers for a literature review by title and abstract.
Judge every paper only by the text given; do not search the web. Reply with JSON only, no other text:
{"papers": [{"id": 1, "verdict": "include", "why": "..."}, ...]}
- verdict: "include" (clearly on the topic), "maybe" (partly, or the abstract is missing or unclear),
  "exclude" (off the topic, wrong object or wrong kind of publication);
- why: at most 15 words in {lang_name}, concrete (what the paper is about and why it fits or not);
- one entry for every paper id, nothing else."""


def screen(provider: str, keys: dict, articles: list[dict], topic: str, lang: str = "ru") -> tuple[dict, float]:
    """articles: [{"doi", "title", "abstract"}] (one batch). -> ({doi: {"verdict", "why"}}, cost)."""
    lang_name = "Russian" if lang == "ru" else "English"
    parts = [f"What the review is about: {str(topic or '').strip()[:1500]}", ""]
    for i, art in enumerate(articles, 1):
        abstract = re.sub(r"\s+", " ", str(art.get("abstract") or "")).strip()[:ABSTRACT_CHARS] or "(no abstract)"
        parts.append(f"Paper {i}\nTitle: {str(art.get('title') or '').strip()[:400]}\nAbstract: {abstract}\n")
    answer = ask(provider, keys, SCREEN_INSTRUCTIONS.replace("{lang_name}", lang_name), "\n".join(parts),
                 search=False, max_tokens=80 * len(articles) + 200, lang=lang, schema=SCREEN_SCHEMA)
    data = _json_part(answer["text"])
    if isinstance(data, dict):
        data = data.get("papers") or data.get("results") or [data]
    out = {}
    for entry in data if isinstance(data, list) else []:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("id"))
        except (TypeError, ValueError):
            continue
        verdict = str(entry.get("verdict") or "").strip().lower()
        if 1 <= idx <= len(articles) and verdict in VERDICTS:
            out[articles[idx - 1]["doi"]] = {"verdict": verdict, "why": str(entry.get("why") or "").strip()[:160]}
    if not out:
        raise AIError("bad_answer", tr("ИИ ответил не по форме. Нажмите кнопку ещё раз."))
    return out, float(answer["cost"] or 0)
