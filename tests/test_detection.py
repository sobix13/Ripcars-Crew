import unittest
from types import SimpleNamespace

from ripcars_crew.config import defaults
from ripcars_crew.detection import BurstTracker, hosts, inspect, matches_word, normalize, redact, within


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.guard = defaults()["guard"]

    def rules(self, text, **kwargs):
        return {hit.rule for hit in inspect(text, self.guard, **kwargs)}

    def test_nfkc_and_zero_width(self):
        self.assertEqual(normalize("ＦＲＥＥ\u200b NITRO"), "free nitro")

    def test_exact_word_no_innocent_substring(self):
        rule = {"text": "hi", "mode": "exact"}
        self.assertTrue(matches_word("Say HI!", rule))
        self.assertFalse(matches_word("high wheels", rule))

    def test_phrase_whitespace(self):
        self.assertTrue(matches_word("seed  phrase!", {"text": "seed phrase", "mode": "phrase"}))

    def test_contains_explicit(self):
        self.assertTrue(matches_word("training", {"text": "train", "mode": "contains"}))

    def test_regex_is_not_executed(self):
        self.assertFalse(matches_word("a" * 4000, {"text": "(a+)+$", "mode": "contains"}))

    def test_words_configured(self):
        self.guard["words"] = [{"text": "bad phrase", "mode": "phrase"}]
        self.assertIn("words", self.rules("a bad phrase here"))

    def test_disabled_filter(self):
        self.guard["words"] = [{"text": "bad", "mode": "exact"}]
        self.guard["filters"]["words"]["enabled"] = False
        self.assertNotIn("words", self.rules("bad"))

    def test_domain_allow_boundary(self):
        self.assertTrue(within("app.ripcars.io", ["ripcars.io"]))
        self.assertFalse(within("ripcars.io.evil.com", ["ripcars.io"]))
        self.assertFalse(within("notripcars.io", ["ripcars.io"]))

    def test_url_credentials_real_destination(self):
        self.assertEqual(hosts("https://app.ripcars.io@evil.com/")[0][1], "evil.com")
        self.assertIn("lookalike", self.rules("https://app.ripcars.io@evil.com/"))

    def test_official_safe(self):
        self.assertNotIn("lookalike", self.rules("https://app.ripcars.io/packs"))

    def test_lookalike(self):
        self.assertIn("lookalike", self.rules("https://ripcars.io.evil.com"))

    def test_unicode_lookalike(self):
        self.assertIn("lookalike", self.rules("https://rіpcars.io"))

    def test_blocked_domain_subdomains(self):
        self.guard["blocked_domains"] = ["evil.com"]
        self.assertIn("blocked_domain", self.rules("https://www.evil.com"))

    def test_allow_does_not_override_block(self):
        self.guard["blocked_domains"] = ["ripcars.io"]
        self.assertIn("blocked_domain", self.rules("https://app.ripcars.io"))

    def test_masked_link_mismatch(self):
        self.assertIn("masked_link", self.rules("[https://app.ripcars.io](https://evil.com)"))

    def test_plain_label_not_masked_false_positive(self):
        self.assertNotIn("masked_link", self.rules("[view pack](https://app.ripcars.io)"))

    def test_secret_request(self):
        self.assertIn("secret_request", self.rules("Please send your seed phrase to verify"))

    def test_gift_with_link(self):
        self.assertIn("gift_scam", self.rules("Claim free nitro https://evil.com"))

    def test_gift_without_link(self):
        self.assertNotIn("gift_scam", self.rules("My gift was a Hot Wheels car"))

    def test_executable_attachment(self):
        self.assertIn("attachment", self.rules("", attachments=[SimpleNamespace(filename="car.png.EXE")]))

    def test_image_attachment_safe(self):
        self.assertNotIn("attachment", self.rules("", attachments=[SimpleNamespace(filename="car.jpg")]))

    def test_mentions(self):
        self.assertIn("mentions", self.rules("hello", mentions=6))

    def test_caps_disabled_then_enabled(self):
        self.assertNotIn("caps", self.rules("A" * 40))
        self.guard["filters"]["caps"]["enabled"] = True
        self.assertIn("caps", self.rules("A" * 40))

    def test_protected_name(self):
        self.assertIn("impersonation", self.rules("hi", display_name="Rip Cars Support"))
        self.assertNotIn("impersonation", self.rules("hi", display_name="Rip Cars Support", is_staff=True))

    def test_redacts_secrets(self):
        self.assertNotIn("secret123", redact("password: secret123"))
        self.assertNotIn("a" * 64, redact("private value " + "a" * 64))

    def test_burst_flood(self):
        tracker = BurstTracker()
        for n in range(7):
            hits = tracker.record(1, 2, f"message {n}", self.guard, now=n)
        self.assertIn("flood", {h.rule for h in hits})

    def test_duplicate_window(self):
        tracker = BurstTracker()
        for n in range(4):
            hits = tracker.record(1, 2, "same", self.guard, now=n)
        self.assertIn("duplicate", {h.rule for h in hits})
        self.assertFalse(tracker.record(1, 2, "same", self.guard, now=60))

    def test_burst_bounded(self):
        tracker = BurstTracker(max_members=3)
        for user in range(10):
            tracker.record(1, user, "hi", self.guard, now=1)
        self.assertEqual(len(tracker.rows), 3)

    def test_invite_opt_in(self):
        self.assertNotIn("invite", self.rules("https://discord.gg/example"))
        self.guard["filters"]["invite"]["enabled"] = True
        self.assertIn("invite", self.rules("https://discord.gg/example"))

    def test_newline_opt_in(self):
        self.guard["filters"]["newlines"]["enabled"] = True
        self.assertIn("newlines", self.rules("a\n" * 20))
