"""Guards the model table in main.py: every model has one consistent description pattern and a usable download."""
import ast
import os
import re
import unittest

SRC = open(os.path.join(os.path.dirname(__file__), "..", "main.py"), encoding="utf-8").read()


def _literal(name):
    for node in ast.parse(SRC).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(name + " not found in main.py")


MODELS = _literal("MODELS")
URLS = _literal("MODEL_URLS")


class ModelTable(unittest.TestCase):
    def test_names_are_plain_and_unique(self):
        for name in MODELS:
            self.assertRegex(name, r"^[a-z0-9][a-z0-9._-]*$", name)
        self.assertEqual(len(MODELS), len(set(MODELS)))

    def test_default_model_exists(self):
        self.assertIn(_literal("DEFAULT_MODEL"), MODELS)

    def test_sizes_are_real_downloads(self):
        for name, (mb, _d) in MODELS.items():
            self.assertTrue(30 <= mb <= 4000, f"{name}: {mb} MB")

    def test_description_says_languages_and_accuracy(self):
        for name, (_mb, d) in MODELS.items():
            self.assertTrue(d.startswith(("English only.", "Many languages.")), f"{name}: {d[:30]}")
            self.assertIn("accuracy", d.lower(), name)
            self.assertLessEqual(len(d), 175, f"{name}: description too long for the panel")

    def test_english_only_flag_matches_the_name(self):
        for name, (_mb, d) in MODELS.items():
            english = ".en" in name or name.startswith("distil-")
            self.assertEqual(d.startswith("English only."), english, name)

    def test_estimates_are_labelled(self):
        for name, (_mb, d) in MODELS.items():
            if re.search(r"q[58]_[01]|distil|turbo", name) and "Memory about" in d:
                self.assertIn("(est.)", d, f"{name}: memory is an estimate and must say so")

    def test_extra_urls_belong_to_listed_models(self):
        for name, url in URLS.items():
            self.assertIn(name, MODELS)
            self.assertTrue(url.startswith("https://huggingface.co/"), name)


if __name__ == "__main__":
    unittest.main()
