import copy
import unittest

from ripcars_crew import config
from ripcars_crew.commands import duration


class ConfigTests(unittest.TestCase):
    def test_defaults_validate(self):
        config.validate(config.defaults())

    def test_defaults_are_independent(self):
        cfg = config.defaults()
        cfg["guard"]["words"].append({"text": "a", "mode": "exact"})
        self.assertEqual(config.defaults()["guard"]["words"], [])

    def test_unknown_key_rejected(self):
        cfg = config.defaults()
        cfg["root_access"] = True
        with self.assertRaises(ValueError):
            config.validate(cfg)

    def test_bool_not_int(self):
        with self.assertRaises(ValueError):
            config.set_path(config.defaults(), "guard.timeout_seconds", True)

    def test_timeout_limit(self):
        with self.assertRaises(ValueError):
            config.set_path(config.defaults(), "guard.timeout_seconds", 2419201)

    def test_invalid_color(self):
        with self.assertRaises(ValueError):
            config.set_path(config.defaults(), "color", "burgundy")

    def test_https_only(self):
        with self.assertRaises(ValueError):
            config.set_path(config.defaults(), "website", "http://example.com")

    def test_url_credentials_rejected(self):
        with self.assertRaises(ValueError):
            config.set_path(config.defaults(), "website", "https://user:password@example.com")

    def test_duplicate_word_rejected(self):
        with self.assertRaises(ValueError):
            config.set_path(config.defaults(), "guard.words", [{"text": "Word", "mode": "exact"}, {"text": "word", "mode": "exact"}])

    def test_regex_mode_rejected(self):
        with self.assertRaises(ValueError):
            config.set_path(config.defaults(), "guard.words", [{"text": "word", "mode": "regex"}])

    def test_domain_path_rejected(self):
        with self.assertRaises(ValueError):
            config.set_path(config.defaults(), "guard.blocked_domains", ["https://evil.com/path"])

    def test_roles_unique(self):
        with self.assertRaises(ValueError):
            config.set_path(config.defaults(), "moderator_roles", [123, 123])

    def test_topics_duplicate_key(self):
        cfg = config.defaults()
        cfg["tickets"]["categories"].append(copy.deepcopy(cfg["tickets"]["categories"][0]))
        with self.assertRaises(ValueError):
            config.validate(cfg)

    def test_safe_default_no_enforcement(self):
        cfg = config.defaults()
        self.assertFalse(cfg["guard"]["enabled"])
        self.assertFalse(cfg["tickets"]["enabled"])
        self.assertFalse(cfg["native"]["enabled"])
        self.assertEqual(cfg["tickets"]["auto_delete_days"], 0)

    def test_native_cannot_bypass_observe(self):
        cfg = config.defaults()
        cfg["guard"].update(enabled=True, mode="observe")
        cfg["native"]["enabled"] = True
        with self.assertRaises(ValueError):
            config.validate(cfg)

    def test_parse_type(self):
        cfg = config.parse_value(config.defaults(), "guard.enabled", "true")
        self.assertTrue(cfg["guard"]["enabled"])
        with self.assertRaises(ValueError):
            config.parse_value(cfg, "guard.enabled", "yes")

    def test_set_path_does_not_mutate(self):
        cfg = config.defaults()
        other = config.set_path(cfg, "brand", "Rip Cars Club")
        self.assertNotEqual(other["brand"], cfg["brand"])

    def test_duration(self):
        self.assertEqual(duration("3d"), 259200)
        self.assertEqual(duration("6h"), 21600)
        for value in ("29d", "0m", "10years", "-2h"):
            with self.assertRaises(ValueError):
                duration(value)
