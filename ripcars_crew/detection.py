"""Bounded local rules: signals, not a claim of universal scam detection."""
from __future__ import annotations

import re
import time
import unicodedata
from collections import OrderedDict, deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import urlsplit

from .config import domain


URL = re.compile(r"(?:https?://|www\.)[^\s<>]+|(?:[\w-]+\.)+(?:[a-zA-Z]{2,24}|xn--[a-zA-Z0-9-]+)(?:/[^\s<>]*)?", re.I)
MASKED = re.compile(r"\[([^\]\n]{1,300})\]\((https?://[^\s)]+)\)", re.I)
CONFUSABLES = str.maketrans({"а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "х": "x", "і": "i", "ѕ": "s", "Α": "a", "Ο": "o", "Ρ": "p", "С": "c", "Ε": "e", "Ι": "i"})


def normalize(text):
    return "".join(char for char in unicodedata.normalize("NFKC", text) if unicodedata.category(char) != "Cf").casefold()


def redact(text):
    # Never put a posted recovery phrase/private-key payload in previews/logs.
    text = re.sub(r"(?i)\b(?:seed phrase|recovery phrase|private key|password)\s*[:=]\s*[^\n]+", "[sensitive value redacted]", text)
    text = re.sub(r"\b(?:0x)?[a-fA-F0-9]{64}\b", "[key-like value redacted]", text)
    return text


def hosts(text):
    result = []
    for match in URL.finditer(normalize(text)[:8000]):
        raw = match.group().rstrip(".,;!?'\"`)]}")
        try:
            parsed = urlsplit(raw if "://" in raw else "https://" + raw)
            host = domain(parsed.hostname or "")
        except (ValueError, UnicodeError):
            continue
        result.append((raw, host, bool(parsed.username)))
        if len(result) >= 30:
            break
    return result


def within(host, allowed):
    return any(host == domain(item) or host.endswith("." + domain(item)) for item in allowed)


def matches_word(text, rule):
    text, needle = normalize(text), normalize(rule["text"].strip())
    if rule["mode"] == "contains":
        return needle in text
    if rule["mode"] == "phrase":
        text, needle = " ".join(text.split()), " ".join(needle.split())
    return bool(re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", text))


@dataclass(frozen=True)
class Hit:
    rule: str
    reason: str


def inspect(text, guard, *, mentions=0, attachments=(), display_name="", is_staff=False):
    normalized = normalize(text[:8000])
    urls = hosts(normalized)
    hits = []
    def hit(key, reason):
        if guard["filters"][key]["enabled"] and not any(item.rule == key for item in hits):
            hits.append(Hit(key, reason))
    for word in guard["words"]:
        if matches_word(normalized, word):
            hit("words", "Configured word rule matched")
            break
    for raw, host, credentials in urls:
        if within(host, guard["blocked_domains"]):
            hit("blocked_domain", "Configured blocked domain")
        if within(host, guard["allowed_domains"]):
            continue
        if credentials:
            hit("lookalike", "URL contains user-info that can hide its real destination")
        try:
            decoded = host.encode("ascii").decode("idna")
        except UnicodeError:
            decoded = host
        skeleton = normalize(decoded.translate(CONFUSABLES))
        for official in guard["official_domains"]:
            official = domain(official)
            stem = official.split(".")[0]
            labels = skeleton.split(".")
            similar = any(len(label) >= max(4, len(stem) - 2) and SequenceMatcher(None, label, stem).ratio() >= 0.8 for label in labels)
            if official in skeleton or similar or stem in skeleton:
                hit("lookalike", "Possible official-domain lookalike; moderator review required")
    for label, destination in MASKED.findall(normalized):
        shown, target = hosts(label), hosts(destination)
        if shown and target and shown[0][1] != target[0][1]:
            hit("masked_link", "Displayed link domain differs from destination")
    if re.search(r"\b(?:send|share|enter|provide|verify|paste)\b.{0,100}\b(?:seed phrase|recovery phrase|private key|password)\b", normalized):
        hit("secret_request", "Possible request for account or wallet secrets")
    if urls and re.search(r"\b(?:free|claim|gift|giveaway)\b.{0,70}\b(?:nitro|airdrop|reward|gift)\b", normalized):
        hit("gift_scam", "Possible gift/reward lure with a link; review required")
    if any(host in ("discord.gg", "discord.com") and (host == "discord.gg" or "/invite/" in raw) for raw, host, _ in urls):
        hit("invite", "Discord invite link")
    extensions = {v.lower().lstrip(".") for v in guard["blocked_extensions"]}
    if any(str(getattr(a, "filename", a)).lower().rsplit(".", 1)[-1] in extensions for a in attachments):
        hit("attachment", "Blocked executable/script attachment extension")
    if mentions >= guard["mention_limit"] or "@everyone" in normalized or "@here" in normalized:
        hit("mentions", "Mass mentions")
    letters = [char for char in text[:8000] if char.isalpha()]
    if len(letters) >= guard["caps_min_letters"] and sum(c.isupper() for c in letters) / len(letters) >= guard["caps_ratio"]:
        hit("caps", "Excessive capital letters")
    if text.count("\n") >= guard["newline_limit"]:
        hit("newlines", "Excessive line breaks")
    name = normalize(display_name).translate(CONFUSABLES)
    if not is_staff and name in {normalize(v).translate(CONFUSABLES) for v in guard["protected_names"]}:
        hit("impersonation", "Display name matches a protected staff name; review required")
    return hits


class BurstTracker:
    """A bounded sliding window per guild/member; reset on restart by design."""
    def __init__(self, max_members=5000):
        self.rows = OrderedDict()
        self.max_members = max_members

    def record(self, guild, user, text, guard, now=None):
        now = time.monotonic() if now is None else now
        key = (guild, user)
        row = self.rows.pop(key, deque(maxlen=101))
        cutoff = now - max(guard["flood_seconds"], guard["duplicate_seconds"])
        while row and row[0][0] < cutoff:
            row.popleft()
        row.append((now, normalize(text)[:2000]))
        self.rows[key] = row
        while len(self.rows) > self.max_members:
            self.rows.popitem(last=False)
        hits = []
        if guard["filters"]["flood"]["enabled"] and sum(at >= now - guard["flood_seconds"] for at, _ in row) >= guard["flood_count"]:
            hits.append(Hit("flood", "Message flood"))
        value = normalize(text)[:2000]
        if value and guard["filters"]["duplicate"]["enabled"] and sum(at >= now - guard["duplicate_seconds"] and body == value for at, body in row) >= guard["duplicate_count"]:
            hits.append(Hit("duplicate", "Repeated message spam"))
        return hits
