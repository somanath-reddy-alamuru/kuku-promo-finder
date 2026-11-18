"""The model client against a local fake HTTP server. Proves caching, retries and error
handling work. It says nothing about the quality of any real model's answers."""
import json, os, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from promo_finder.llm import LLM, _loads

os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
KEY = "sk-test-secret-123"


class Server:
    """Replies with the next item of `script` each time: an int is an HTTP status, a str is message content."""
    def __init__(self, script):
        self.script, self.hits = list(script), 0
        server = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                server.hits += 1
                item = server.script.pop(0) if server.script else '{"ok": 1}'
                if isinstance(item, int):
                    self.send_response(item)
                    self.end_headers()
                    self.wfile.write(f"error, your key was {KEY}".encode())
                    return
                body = {"choices": [{"message": {"content": item}}],
                        "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps(body).encode())

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.httpd.server_port}"

    def close(self):
        self.httpd.shutdown()


class LLMTests(unittest.TestCase):
    def client(self, server, **kw):
        self.tmp = tempfile.mkdtemp()
        return LLM(server.url, "m", KEY, cache_dir=self.tmp, pause=0, log=lambda m: None,
                   sleep=lambda s: None, **kw)

    def test_cache_hit_sends_nothing(self):
        s = Server(['{"a": 1}'])
        llm = self.client(s)
        self.assertEqual(llm.ask_json("sys", "user", "t"), {"a": 1})
        self.assertEqual(llm.ask_json("sys", "user", "t"), {"a": 1})
        self.assertEqual((s.hits, llm.stats["calls"], llm.stats["cached"]), (1, 1, 1))
        self.assertEqual(llm.stats["input_tokens"], 20)        # cached calls still count tokens
        saved = open(os.path.join(self.tmp, os.listdir(self.tmp)[0])).read()
        self.assertNotIn(KEY, saved)
        s.close()

    def test_retries_rate_limit_then_succeeds(self):
        s = Server([429, 503, '{"a": 2}'])
        llm = self.client(s)
        self.assertEqual(llm.ask_json("s", "u", "t"), {"a": 2})
        self.assertEqual((s.hits, llm.stats["retries"], llm.stats["failed"]), (3, 2, 0))
        s.close()

    def test_invalid_json_is_retried(self):
        s = Server(["sorry, here you go", '```json\n{"a": 3}\n```'])
        llm = self.client(s)
        self.assertEqual(llm.ask_json("s", "u", "t"), {"a": 3})
        self.assertEqual(llm.stats["bad_json"], 1)
        s.close()

    def test_gives_up_and_caches_nothing(self):
        s = Server([500, 500, 500])
        llm = self.client(s, attempts=3)
        self.assertIsNone(llm.ask_json("s", "u", "t"))
        self.assertEqual(llm.stats["failed"], 1)
        self.assertEqual(os.listdir(self.tmp), [])            # a re-run will try again
        s.close()

    def test_bad_key_stops_with_clear_message_and_no_key(self):
        s = Server([401])
        llm = self.client(s)
        with self.assertRaises(RuntimeError) as e:
            llm.ask_json("s", "u", "t")
        self.assertIn("refused the API key", str(e.exception))
        self.assertNotIn(KEY, str(e.exception))
        s.close()

    def test_other_errors_redact_the_key(self):
        s = Server([404])
        llm = self.client(s)
        with self.assertRaises(RuntimeError) as e:
            llm.ask_json("s", "u", "t")
        self.assertNotIn(KEY, str(e.exception))
        s.close()

    def test_loads(self):
        self.assertEqual(_loads('Here: {"x": 1} done'), {"x": 1})
        self.assertIsNone(_loads("[1, 2]"))
        self.assertIsNone(_loads("no json"))


class CacheIsolationTests(unittest.TestCase):
    def test_provider_and_model_are_part_of_cache_key(self):
        import tempfile
        from unittest.mock import patch
        from promo_finder.llm import LLM

        class Response:
            status_code = 200
            text = ""
            def json(self):
                return {"choices": [{"message": {"content": '{"ok": true}'}}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

        with tempfile.TemporaryDirectory() as d, patch("promo_finder.llm.requests.post", return_value=Response()) as post:
            one = LLM("gemini", "model-a", "key", cache_dir=d, pause=0, sleep=lambda _: None)
            two = LLM("groq", "model-a", "key", cache_dir=d, pause=0, sleep=lambda _: None)
            three = LLM("gemini", "model-b", "key", cache_dir=d, pause=0, sleep=lambda _: None)
            self.assertEqual(one.ask_json("sys", "user", "tag"), {"ok": True})
            self.assertEqual(two.ask_json("sys", "user", "tag"), {"ok": True})
            self.assertEqual(three.ask_json("sys", "user", "tag"), {"ok": True})
            self.assertEqual(post.call_count, 3)
