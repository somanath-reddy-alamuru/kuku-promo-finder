"""The only file that knows which AI model we use.

Any provider with an OpenAI-style /chat/completions endpoint works (Gemini, Groq,
OpenRouter, OpenAI, a local Ollama...). To swap the model, change three settings
(environment variables, or the fields in the app's sidebar):

    LLM_BASE_URL   e.g. https://generativelanguage.googleapis.com/v1beta/openai
    LLM_MODEL      the model name the provider uses
    LLM_API_KEY    your key

Every answer is cached on disk, so a re-run costs nothing and a crashed run resumes.
"""
import hashlib, json, os, re, time
import requests

PRESETS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
}


class LLM:
    def __init__(self, base_url=None, model=None, api_key=None, cache_dir="cache",
                 pause=6.0, log=print, attempts=8, sleep=time.sleep):
        base_url = base_url or os.getenv("LLM_BASE_URL", "gemini")
        self.base_url = PRESETS.get(base_url, base_url).rstrip("/")
        self.model = model or os.getenv("LLM_MODEL", "")
        self.api_key = api_key or os.getenv("LLM_API_KEY", "")
        self.cache_dir, self.pause, self.log = cache_dir, pause, log
        self.attempts, self._sleep = attempts, sleep
        self.stats = {"calls": 0, "cached": 0, "input_tokens": 0, "output_tokens": 0,
                      "bad_json": 0, "failed": 0, "retries": 0, "seconds": 0.0, "errors": []}
        self._last = 0.0
        self._json_mode = True
        os.makedirs(cache_dir, exist_ok=True)

    def _count(self, usage):
        # tokens are counted for cached answers too, so run_log.json always shows the cost of a full run
        self.stats["input_tokens"] += usage.get("prompt_tokens", 0)
        self.stats["output_tokens"] += usage.get("completion_tokens", 0)

    def _error(self, message):
        self.stats["errors"] = (self.stats["errors"] + [message])[-20:]

    def list_models(self):
        r = requests.get(self.base_url + "/models",
                         headers={"Authorization": f"Bearer {self.api_key}"}, timeout=60)
        r.raise_for_status()
        return sorted(m["id"] for m in r.json().get("data", []))

    def ask_json(self, system, user, tag=""):
        """Send one prompt, get parsed JSON back (or None if the model never produced valid JSON)."""
        # Provider and model are part of the key: the same prompt sent to another
        # provider/model must never reuse a previous provider's answer.
        key_material = f"{self.base_url}\n{self.model}\n{system}\n{user}"
        key = hashlib.sha256(key_material.encode()).hexdigest()[:20]
        safe_tag = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(tag))[:80] or "call"
        path = os.path.join(self.cache_dir, f"{safe_tag}_{key}.json")
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    saved = json.load(f)
                parsed = saved.get("parsed")
                if isinstance(parsed, dict):
                    self.stats["cached"] += 1
                    self._count(saved.get("usage") or {})
                    return parsed
            except (OSError, ValueError, TypeError):
                # A partial/corrupted cache file is ignored and replaced by a fresh call.
                pass

        for attempt in range(self.attempts):
            if attempt:
                self.stats["retries"] += 1
            wait = self.pause - (time.time() - self._last)   # stay under free-tier requests/minute
            if wait > 0:
                self._sleep(wait)
            body = {"model": self.model, "temperature": 0.2,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": user}]}
            if self._json_mode:
                body["response_format"] = {"type": "json_object"}
            t = time.time()
            try:
                r = requests.post(self.base_url + "/chat/completions", json=body, timeout=180,
                                  headers={"Authorization": f"Bearer {self.api_key}"})
            except requests.RequestException as e:
                delay = min(60, 10 * (attempt + 1))
                self._error(f"no connection ({type(e).__name__})")
                self.log(f"  no connection ({type(e).__name__}); retrying in {delay}s")
                self._sleep(delay)
                continue
            self._last = time.time()
            self.stats["seconds"] += self._last - t
            if r.status_code == 429 or r.status_code >= 500:      # rate limit / busy: back off
                delay = min(60, 15 * (attempt + 1))
                self._error(f"HTTP {r.status_code} from the provider")
                self.log(f"  {r.status_code} from provider; waiting {delay}s")
                self._sleep(delay)
                continue
            if r.status_code == 400 and self._json_mode:          # provider without JSON mode
                self._json_mode = False
                continue
            if r.status_code in (401, 403):
                raise RuntimeError(f"the provider refused the API key (HTTP {r.status_code}). "
                                   "Check the key and that it is for this provider.")
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code} from {self.base_url}: {_redact(r.text, self.api_key)}")
            try:
                data = r.json()
                raw = data["choices"][0]["message"].get("content") or ""
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                self.stats["bad_json"] += 1
                self._error("the provider sent a reply in an unexpected shape")
                continue
            usage = data.get("usage") or {}
            self.stats["calls"] += 1
            self._count(usage)
            parsed = _loads(raw)
            if parsed is None:
                self.stats["bad_json"] += 1
                self._error("the model's answer was not valid JSON")
                self.log("  model returned invalid JSON; retrying")
                continue
            payload = {"model": self.model, "provider": self.base_url, "tag": tag, "usage": usage,
                       "system": system, "user": user, "raw": raw, "parsed": parsed}
            temp_path = path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as f:  # API key is never written to disk.
                json.dump(payload, f, ensure_ascii=False, indent=1)
            os.replace(temp_path, path)
            return parsed
        # gave up on this call. Nothing is cached, so running again retries only the failed calls.
        self.stats["failed"] += 1
        self._error(f"gave up on {tag} after {self.attempts} attempts")
        self.log(f"  GAVE UP on {tag}: run again later to fill it in")
        return None


def _redact(text, key):
    text = (text or "")[:400]
    return text.replace(key, "***") if key else text


def _loads(raw):
    """Models sometimes wrap JSON in ``` fences or add a sentence around it."""
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    for candidate in (raw, raw[raw.find("{"): raw.rfind("}") + 1]):
        try:
            out = json.loads(candidate)
            if isinstance(out, dict):
                return out
        except (ValueError, TypeError):
            pass
    return None
