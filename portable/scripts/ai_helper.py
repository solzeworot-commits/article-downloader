# -*- coding: utf-8 -*-
# © 2026 Bucaneiro. Бесплатная программа; условия — в файле ЛИЦЕНЗИЯ.txt.
"""
AI helper on the user's own Perplexity API key (v7.6, Agent API since 7.6.1).

Each user registers at Perplexity, tops up the API balance and pastes the key
into the program; it lives in keys.json with the other keys and is sent only to
api.perplexity.ai. The author's key is never used.

Two jobs:
  suggest_search(topic)  - a topic in the user's own words -> English search
                           concepts with synonyms, one concept per line
                           ("a; b; c"), which doi_search turns into Scopus,
                           Web of Science and OpenAlex queries;
  screen(articles, topic) - a verdict per abstract (include / maybe /
                           exclude) with a short reason. It is only advice:
                           the decision stays with the user.

The client speaks the Agent API (POST /v1/responses, preset "fast-search"),
with a JSON schema for the answers that the program parses. Perplexity
retired Sonar chat completions in autumn 2026: they answer 403
"chat_completions_not_available" (seen with a real key in November 2026).
The old endpoint (POST /chat/completions, model "sonar") is kept only as a
fallback in case the Agent API answers 404. A server that refuses the
response_format is asked again without it. Shapes follow the official
perplexityai Python SDK 0.42 (July 2026).
ARTICLE_AI_CHAT_URL and ARTICLE_AI_AGENT_URL override the endpoints for tests.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from i18n import tr

CHAT_URL = "https://api.perplexity.ai/chat/completions"
AGENT_URL = "https://api.perplexity.ai/v1/responses"
CHAT_MODEL = "sonar"
AGENT_PRESET = "fast-search"
USER_AGENT = "ArticleDownloader (+https://github.com/solzeworot-commits/article-downloader)"
SCREEN_BATCH = 10
ABSTRACT_CHARS = 1600
VERDICTS = ("include", "maybe", "exclude")

# Which endpoint answered last time ("agent" or "chat") and the optional
# parameters a server refused (they are not sent again).
_endpoint = {"name": "agent", "refused": set()}
OPTIONAL = ("response_format", "language_preference", "max_output_tokens", "max_tokens", "search_mode")
# Words of an answer that say "this endpoint is gone, use the other one".
GONE_MARKERS = ("chat_completions_not_available", "/v1/responses", "agent api", "migrate-from-sonar")


class AIError(Exception):
    """kind: no_key, bad_key, no_money, quota, network, bad_answer, other.
    The message is for the user."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _post(url: str, key: str, body: dict, timeout: float) -> tuple[int, dict | None, str]:
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "Accept": "application/json", "User-Agent": USER_AGENT})
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
        raise AIError("network", tr("Нет связи с Perplexity ({reason}). Проверьте интернет.", reason=reason)) from None
    try:
        data = json.loads(text) if text else None
    except ValueError:
        data = None
    return status, data if isinstance(data, dict) else None, text


def _error_text(data: dict | None, text: str) -> str:
    err = (data or {}).get("error")
    if isinstance(err, dict):
        detail = " ".join(str(err.get(k) or "") for k in ("message", "type", "code"))
    elif err:
        detail = str(err)
    else:
        detail = str((data or {}).get("detail") or (data or {}).get("message") or text)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", detail)).strip()[:300]


def _http_error(status: int, data: dict | None, text: str) -> AIError:
    detail = _error_text(data, text)
    low = detail.lower()
    if status == 402 or any(w in low for w in ("credit", "balance", "insufficient", "billing", "payment", "quota")):
        return AIError("no_money", tr("На счёте Perplexity API нет денег. Пополните баланс: страница API → "
                                      "«Добавить кредиты» (шаг 5 инструкции)."))
    if status == 401:
        return AIError("bad_key", tr("Ключ Perplexity неверный — скопируйте его ещё раз, целиком (начинается с pplx-)."))
    if status == 403:
        return AIError("bad_key", tr("Perplexity не принял ключ (код 403). {detail}", detail=detail).strip())
    if status == 429:
        return AIError("quota", tr("Слишком много запросов к Perplexity подряд. Подождите минуту и повторите."))
    if status >= 500:
        return AIError("other", tr("Perplexity временно не отвечает (код {code}). Попробуйте позже.", code=status))
    return AIError("other", tr("Perplexity ответил ошибкой {code}. {detail}", code=status, detail=detail).strip())


def _chat_answer(data: dict) -> dict:
    choices = data.get("choices") or []
    message = (choices[0] or {}).get("message") if choices else None
    text = str((message or {}).get("content") or "")
    sources = [{"title": str(r.get("title") or ""), "url": str(r.get("url") or "")}
               for r in data.get("search_results") or [] if isinstance(r, dict)]
    cost = (((data.get("usage") or {}).get("cost")) or {}).get("total_cost")
    return {"text": text, "sources": sources, "cost": cost}


def _agent_answer(data: dict) -> dict:
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
        raise AIError("other", tr("Perplexity не смог ответить: {detail}", detail=str(err.get("message") or "?")[:200]))
    cost = (((data.get("usage") or {}).get("cost")) or {}).get("total_cost")
    return {"text": "".join(texts), "sources": sources, "cost": cost}


def _gone(name: str, status: int, data: dict | None, raw: str) -> bool:
    """The endpoint does not exist (any more): 404, or the old one sending people to the Agent API."""
    if status in (404, 405, 410):
        return True
    detail = _error_text(data, raw).lower()
    return name == "chat" and status in (400, 403) and any(m in detail for m in GONE_MARKERS)


def _refused_params(body: dict, status: int, data: dict | None, raw: str) -> list[str]:
    """Optional parameters a 400/422 answer complains about."""
    if status not in (400, 422):
        return []
    detail = _error_text(data, raw).lower()
    names = [k for k in OPTIONAL if k in body and k in detail]
    if "response_format" in body and "response_format" not in names and ("json_schema" in detail or "schema" in detail):
        names.append("response_format")
    return names


def ask(key: str, instructions: str, text: str, *, search: bool = True, max_tokens: int = 1200,
        lang: str = "ru", timeout: float = 120, schema: tuple[str, dict] | None = None) -> dict:
    """One question. {"text", "sources": [{"title", "url"}], "cost": dollars or None}.
    schema: (name, JSON schema) of the answer, sent as response_format."""
    key = (key or "").strip()
    if not key:
        raise AIError("no_key", tr("ИИ-помощник не подключён: вставьте ключ Perplexity (кнопка «🤖 ИИ-помощник» на главной)."))
    chat_url = os.environ.get("ARTICLE_AI_CHAT_URL") or CHAT_URL
    agent_url = os.environ.get("ARTICLE_AI_AGENT_URL") or AGENT_URL
    fmt = {"type": "json_schema", "json_schema": {"name": schema[0], "schema": schema[1]}} if schema else None
    agent_body = {"preset": AGENT_PRESET, "instructions": instructions, "input": text,
                  "max_output_tokens": max_tokens, "language_preference": lang}
    chat_body = {"model": CHAT_MODEL, "max_tokens": max_tokens, "language_preference": lang,
                 "messages": [{"role": "system", "content": instructions}, {"role": "user", "content": text}]}
    if search:
        chat_body["search_mode"] = "academic"
    else:
        chat_body["disable_search"] = True

    order = ["agent", "chat"] if _endpoint["name"] == "agent" else ["chat", "agent"]
    for i, name in enumerate(order):
        url, body = (agent_url, dict(agent_body)) if name == "agent" else (chat_url, dict(chat_body))
        if fmt:
            body["response_format"] = fmt
        for field in _endpoint["refused"]:
            body.pop(field, None)
        status, data, raw = _post(url, key, body, timeout)
        for _ in range(3):
            refused = _refused_params(body, status, data, raw)
            if not refused:
                break
            for field in refused:
                _endpoint["refused"].add(field)
                body.pop(field, None)
            status, data, raw = _post(url, key, body, timeout)
        # This endpoint is gone (Perplexity moved Sonar to the Agent API): try the other one.
        if _gone(name, status, data, raw):
            continue
        if status >= 400 or data is None:
            if data is None and status < 400:
                raise AIError("bad_answer", tr("Perplexity прислал непонятный ответ."))
            raise _http_error(status, data, raw)
        _endpoint["name"] = name
        return _chat_answer(data) if name == "chat" else _agent_answer(data)
    raise AIError("other", tr("Perplexity не нашёл ни один из своих адресов API (код 404). Обновите программу."))


def _json_part(text: str):
    """The JSON object or array in an answer, without ```json fences or chatter."""
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

def check_key(key: str) -> dict:
    """A tiny request (well under a cent)."""
    try:
        answer = ask(key, "Reply with the single word OK. Do not search the web.", "ping", search=False,
                     max_tokens=32, timeout=60)
    except AIError as exc:
        return {"ok": False, "kind": exc.kind, "message": exc.message}
    return {"ok": True, "kind": "ok", "message": tr("Ключ работает ✓ (Perplexity отвечает)"),
            "cost": answer.get("cost")}


SEARCH_SCHEMA = ("search_concepts", {
    "type": "object",
    "properties": {
        "concepts": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
        "note": {"type": "string"},
    },
    "required": ["concepts", "note"],
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
        }},
    },
    "required": ["papers"],
})

SEARCH_INSTRUCTIONS = """You help a researcher build a literature search in Scopus, Web of Science and OpenAlex.
The researcher describes the topic in their own words, often in Russian. Look at how real papers on this topic
are titled and indexed, then reply with JSON only, no other text:
{"concepts": [["main term", "synonym", "variant"], ["second concept", "synonym"]], "note": "..."}
Rules:
- 2 to 4 concepts; every concept is a list of 1 to 6 English search terms that mean the same thing;
  all concepts must be present in a relevant paper (they will be joined with AND, synonyms with OR);
- terms are short phrases used in titles and abstracts, no quotes, no wildcards, no boolean operators;
- put the most important concept first; leave out generic words like "study", "analysis", "effect";
- "note": one or two short sentences in {lang_name} that explain the choice or warn what may be missed."""


def suggest_search(key: str, topic: str, lang: str = "ru") -> dict:
    """{"lines": ["a; b", "c; d"], "note", "sources", "cost"}."""
    topic = re.sub(r"\s+", " ", str(topic or "")).strip()
    if len(topic) < 3:
        raise AIError("bad_answer", tr("Опишите тему хотя бы парой слов."))
    lang_name = "Russian" if lang == "ru" else "English"
    answer = ask(key, SEARCH_INSTRUCTIONS.replace("{lang_name}", lang_name), topic[:1500], search=True,
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


SCREEN_INSTRUCTIONS = """You help a researcher pre-screen papers for a literature review by title and abstract.
Judge every paper only by the text given; do not search the web. Reply with JSON only, no other text:
{"papers": [{"id": 1, "verdict": "include", "why": "..."}, ...]}
- verdict: "include" (clearly on the topic), "maybe" (partly, or the abstract is missing or unclear),
  "exclude" (off the topic, wrong object or wrong kind of publication);
- why: at most 15 words in {lang_name}, concrete (what the paper is about and why it fits or not);
- one entry for every paper id, nothing else."""


def screen(key: str, articles: list[dict], topic: str, lang: str = "ru") -> tuple[dict, float]:
    """articles: [{"doi", "title", "abstract"}] (one batch). -> ({doi: {"verdict", "why"}}, cost)."""
    lang_name = "Russian" if lang == "ru" else "English"
    parts = [f"What the review is about: {str(topic or '').strip()[:1500]}", ""]
    for i, art in enumerate(articles, 1):
        abstract = re.sub(r"\s+", " ", str(art.get("abstract") or "")).strip()[:ABSTRACT_CHARS] or "(no abstract)"
        parts.append(f"Paper {i}\nTitle: {str(art.get('title') or '').strip()[:400]}\nAbstract: {abstract}\n")
    answer = ask(key, SCREEN_INSTRUCTIONS.replace("{lang_name}", lang_name), "\n".join(parts), search=False,
                 max_tokens=80 * len(articles) + 200, lang=lang, schema=SCREEN_SCHEMA)
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
