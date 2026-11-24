import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import ai_visibility as scorer


class ScoringTests(unittest.TestCase):
    def test_request_payload_for_each_default_engine(self):
        for engine in scorer.DEFAULT_ENGINES:
            with self.subTest(engine=engine["name"]):
                response = {"choices": [{"message": {"content": "Example answer"}}]}
                with patch.object(scorer.urllib.request, "urlopen", return_value=io.StringIO(json.dumps(response))) as post:
                    scorer.openrouter(engine["model"], "Which API?", "test", search_tool=engine.get("search_tool"))
                payload = json.loads(post.call_args.args[0].data)
                self.assertEqual(payload["messages"], [{"role": "user", "content": "Which API?"}])
                if engine["name"] == "chatgpt-search":
                    self.assertEqual(payload["model"], "openai/gpt-5.4-mini")
                    self.assertEqual(payload["tools"], [{
                        "type": "openrouter:web_search", "parameters": {"engine": "exa", "max_uses": 1}
                    }])
                else:
                    self.assertEqual(payload["model"], "perplexity/sonar")
                    self.assertNotIn("tools", payload)

    def test_whole_brand_names(self):
        names = ["Linear", "C++", "Bright Data"]
        self.assertEqual(scorer.named_in("Cost grows linearly.", names), set())
        self.assertEqual(
            scorer.named_in("LINEAR, C++ and Bright Data's SDK.", names),
            set(names),
        )

    def test_hosts_and_subdomain_ownership(self):
        for url in ("https://weave.dev", "https://www.weave.dev:443/docs"):
            self.assertEqual(scorer.host(url), "weave.dev")
        owned = {"weave.dev": "Weave"}
        self.assertEqual(scorer.domain_owner("https://docs.weave.dev/a", owned), "Weave")
        self.assertIsNone(scorer.domain_owner("https://notweave.dev/a", owned))

    def test_partial_run_retains_evidence(self):
        questions = ["How does the cost scale?", "Which issue trackers offer offline support?"]
        seen = []

        def answer(model, prompt, key, *, search_tool=None):
            seen.append(prompt)
            if prompt == questions[0]:
                return "Cost scales linearly.", ["https://weave.dev/docs"]
            if prompt == questions[1]:
                raise TimeoutError("fixture timeout")
            return "Linear is an issue tracker.", []

        with tempfile.TemporaryDirectory() as tmp:
            inputs = Path(tmp) / "questions.txt"
            inputs.write_text("\n".join(questions), encoding="utf-8")
            output = Path(tmp) / "result.json"
            argv = ["ai_visibility.py", "--brand", "Linear", "--category", "issue tracking",
                    "--engines", "perplexity", "--prompt-file", str(inputs),
                    "--concurrency", "1", "--json", str(output)]
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "offline-test"}), \
                 patch.object(sys, "argv", argv), patch.object(scorer, "openrouter", answer), \
                 contextlib.redirect_stdout(io.StringIO()):
                scorer.main()
            report = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(seen, questions + [q.format(brand="Linear") for q in scorer.BRANDED_TEMPLATES])
        self.assertEqual(report["organic_named"]["perplexity"], {
            "named": 0, "total": 1, "attempted": 2, "failed": 1,
        })
        self.assertEqual(report["supply_chain"]["perplexity"], {"weave.dev": 1})
        organic = {r["prompt"]: r for r in report["checks"] if r["kind"] == "organic"}
        self.assertEqual(organic[questions[0]]["content"], "Cost scales linearly.")
        self.assertEqual(organic[questions[0]]["citations"], ["https://weave.dev/docs"])
        self.assertEqual(organic[questions[0]]["model"], "perplexity/sonar")
        self.assertIn("fixture timeout", organic[questions[1]]["err"])

    def test_invalid_questions_stop_before_provider_calls(self):
        for text in ("", "How does Linear work?", "Which tracker?\nWhich tracker?"):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                inputs = Path(tmp) / "questions.txt"
                inputs.write_text(text, encoding="utf-8")
                argv = ["ai_visibility.py", "--brand", "Linear", "--category", "issue tracking",
                        "--prompt-file", str(inputs)]
                with patch.dict(os.environ, {"OPENROUTER_API_KEY": "offline-test"}), \
                     patch.object(sys, "argv", argv), patch.object(scorer, "openrouter") as provider, \
                     contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    scorer.main()
                provider.assert_not_called()

    def test_empty_responses_are_failed_attempts(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "report.json"
            argv = ["ai_visibility.py", "--brand", "Linear", "--category", "issue tracking",
                    "--engines", "perplexity", "--json", str(output)]
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "offline-test"}), \
                 patch.object(sys, "argv", argv), patch.object(scorer, "openrouter", return_value=("", [])), \
                 patch.object(scorer, "generate_prompts", return_value=["Which tracker?"]), \
                 contextlib.redirect_stdout(io.StringIO()):
                scorer.main()
            data = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(data["organic_named"]["perplexity"],
                         {"attempted": 1, "failed": 1, "named": 0, "total": 0})
        self.assertTrue(all(row["err"] and row["content"] is None for row in data["checks"]))


if __name__ == "__main__":
    unittest.main()
