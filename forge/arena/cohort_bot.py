"""Script cohort for Conquest. No model API.

Speaks NDJSON on stdin/stdout. Each line is one engine request; each reply
is ``{"id": ..., "body": {...}}``. The body is an order or a duel answer.
The engine still validates every order.

    python -m forge.arena.cohort_bot
"""
from __future__ import annotations

import json
import re
import sys

from forge.arena.conquest import WORDS

# Fillers with none of e, a, t, n — legal in every lipogram the engine deals.
_SAFE = ["big", "dog", "sky", "owl", "cup", "fly", "mug", "pig", "rum", "box", "fog", "log"]

_INITIALS = {
    "b": ["big", "brave", "brown", "bold", "bright", "busy", "bitter", "blue",
          "broad", "blunt", "brisk", "brawny", "burly", "bumpy", "basic", "brief"],
    "c": ["cold", "calm", "clever", "crimson", "clear", "curved", "crisp", "copper",
          "curious", "cross", "crafty", "cloudy", "chunky", "classy", "cosmic", "cunning"],
    "d": ["dark", "daring", "deep", "dusty", "dry", "dull", "dim", "distant",
          "double", "down", "drab", "dreary", "driven", "dusky", "daily", "dire"],
    "f": ["fast", "fierce", "flat", "fresh", "fine", "firm", "foggy", "faint",
          "fancy", "far", "fat", "few", "fond", "free", "full", "funny"],
    "g": ["great", "grim", "green", "gold", "gray", "grand", "gentle", "giant",
          "glad", "good", "growing", "gloomy", "glossy", "graceful", "gritty", "guilty"],
    "m": ["many", "mighty", "mild", "mad", "main", "massive", "mean", "merry",
          "metal", "milder", "misty", "modern", "modest", "moist", "moral", "muddy"],
    "p": ["proud", "pale", "plain", "prime", "pure", "purple", "pretty", "poor",
          "proudly", "public", "puny", "pushy", "patient", "peaceful", "pink", "polite"],
    "r": ["red", "rough", "round", "rapid", "rare", "raw", "ready", "real",
          "rich", "ripe", "rocky", "royal", "rude", "running", "rusty", "rugged"],
    "s": ["small", "strong", "sharp", "silent", "silver", "slow", "soft", "solid",
          "sore", "sour", "spare", "stark", "steel", "stern", "still", "sunny"],
    "t": ["tall", "tiny", "tough", "true", "thick", "thin", "tight", "tired",
          "total", "tragic", "triple", "tropical", "trusty", "turbulent", "twin", "typical"],
}


def _shift(word: str, k: int) -> str:
    return "".join(chr((ord(ch) - 97 + k) % 26 + 97) for ch in word if ch.isalpha())


def _solve_logic(prompt: str):
    seq = re.search(r"sequence\? (.+), \?", prompt)
    if seq:
        nums = [int(x) for x in re.findall(r"-?\d+", seq.group(1))]
        if len(nums) >= 3:
            d1 = [b - a for a, b in zip(nums, nums[1:])]
            d2 = [b - a for a, b in zip(d1, d1[1:])]
            step = d2[-1] if d2 else 0
            return nums[-1] + d1[-1] + step
    div = re.search(
        r"(\d+) <= n <= (\d+) are divisible by (\d+) but NOT divisible by (\d+)",
        prompt,
    )
    if div:
        lo, hi, k, j = (int(div.group(i)) for i in range(1, 5))
        return sum(1 for n in range(lo, hi + 1) if n % k == 0 and n % j != 0)
    digits = re.search(r"from 1 to (\d+)", prompt)
    if digits:
        n = int(digits.group(1))
        return sum(int(d) for x in range(1, n + 1) for d in str(x))
    mod = re.search(r"\((\d+) \^ (\d+)\) mod (\d+)", prompt)
    if mod:
        return pow(int(mod.group(1)), int(mod.group(2)), int(mod.group(3)))
    return None


def _solve_cipher(prompt: str) -> str | None:
    vig = re.search(r"key '([a-z]+)': '([a-z ]+)'", prompt)
    if vig:
        key, ct = vig.group(1), vig.group(2)
        out, i = [], 0
        for word in ct.split():
            plain = ""
            for ch in word:
                plain += _shift(ch, -(ord(key[i % len(key)]) - 97))
                i += 1
            out.append(plain)
        return " ".join(out)
    atb = re.search(r"reversed: '([a-z ]+)'", prompt)
    if atb:
        words = []
        for word in atb.group(1).split():
            rev = word[::-1]
            words.append("".join(chr(219 - ord(c)) for c in rev))
        return " ".join(words)
    caesar = re.search(r"UNKNOWN size: '([a-z ]+)'", prompt)
    if caesar:
        words = caesar.group(1).split()
        known = set(WORDS)
        for k in range(1, 26):
            plain = [_shift(w, -k) for w in words]
            if all(w in known for w in plain):
                return " ".join(plain)
    return None


def _solve_words(prompt: str) -> str | None:
    initials = re.search(
        r"exactly (\d+) words where every word starts with the letter '([a-z])'",
        prompt,
    )
    if initials:
        n, letter = int(initials.group(1)), initials.group(2)
        bank = _INITIALS.get(letter) or [letter + "old"] * n
        return " ".join(bank[i % len(bank)] for i in range(n)) + "."
    acrostic = re.search(r"spell '([A-Z]+)'", prompt)
    if acrostic:
        lines = [f"{ch.lower()}old dogs run" for ch in acrostic.group(1)]
        return "\n".join(lines)
    lip = re.search(
        r"exactly (\d+) words that never uses the letter '([a-z])' "
        r"and includes the words '([a-z]+)' and '([a-z]+)'",
        prompt,
    )
    if lip:
        n, banned, a, b = int(lip.group(1)), lip.group(2), lip.group(3), lip.group(4)
        fillers = [w for w in _SAFE if banned not in w and w not in (a, b)]
        words = [a, b]
        i = 0
        while len(words) < n and fillers:
            words.append(fillers[i % len(fillers)])
            i += 1
        return " ".join(words[:n]) + "."
    return None


def _solve_code(prompt: str) -> str | None:
    if "strictly greater than" in prompt:
        k = int(re.search(r"greater than (\d+)", prompt).group(1))
        return (
            "def solve(n):\n"
            f"    return sum(int(d) for d in str(abs(int(n))) if int(d) > {k})\n"
        )
    if "run-length" in prompt:
        return (
            "def solve(s):\n"
            "    out, i = [], 0\n"
            "    while i < len(s):\n"
            "        j = i\n"
            "        while j < len(s) and s[j] == s[i]:\n"
            "            j += 1\n"
            "        out.append(s[i] + str(j - i))\n"
            "        i = j\n"
            "    return ''.join(out)\n"
        )
    if "strictly increasing" in prompt:
        return (
            "def solve(xs):\n"
            "    if not xs:\n"
            "        return 0\n"
            "    best = cur = 1\n"
            "    for a, b in zip(xs, xs[1:]):\n"
            "        cur = cur + 1 if b > a else 1\n"
            "        best = max(best, cur)\n"
            "    return best\n"
        )
    if "rotates s LEFT" in prompt:
        k = int(re.search(r"LEFT by (\d+)", prompt).group(1))
        return (
            "def solve(s):\n"
            "    if not s:\n"
            "        return s\n"
            f"    k = {k} % len(s)\n"
            "    return (s[k:] + s[:k]).swapcase()\n"
        )
    if "whitespace-separated" in prompt:
        k = int(re.search(r">= (\d+)", prompt).group(1))
        return (
            "def solve(s):\n"
            f"    return sum(1 for w in s.split() if len(w) >= {k})\n"
        )
    return None


def solve_duel(prompt: str) -> dict:
    domain = "logic"
    found = re.search(r"TASK \((\w+)\)", prompt)
    if found:
        domain = found.group(1)
    if domain == "code":
        code = _solve_code(prompt)
        return {"code": code} if code else {"answer": "0"}
    if domain == "cipher":
        ans = _solve_cipher(prompt)
        return {"answer": ans if ans is not None else "0"}
    if domain == "words":
        ans = _solve_words(prompt)
        return {"answer": ans if ans is not None else "0"}
    ans = _solve_logic(prompt)
    return {"answer": ans if ans is not None else "0"}


def plan_orders(req: dict) -> dict:
    """Legal, fog-respecting orders. Skips hidden tiles so Cheat Watch stays quiet."""
    view = req.get("view") or {}
    me = view.get("faction") or req.get("faction") or "red"
    tiles = {t["name"]: t for t in view.get("territories") or []}
    mine = [name for name, tile in tiles.items() if tile.get("owner") == me]
    amount = int(view.get("reinforcements") or 0)
    if not mine:
        return {
            "reinforce": {}, "attacks": [], "fortify": None, "spy": None,
            "messages": [], "memory": "no land",
        }

    def threats(name: str) -> list[str]:
        return [n for n in tiles[name]["adjacent"] if tiles[n].get("owner") != me]

    host = max(mine, key=lambda n: (len(threats(n)), -(tiles[n].get("troops") or 0)))
    reinforce = {host: amount} if amount else {}
    troops = {n: int(tiles[n].get("troops") or 0) for n in mine}
    troops[host] = troops.get(host, 0) + amount

    cands = []
    for src in mine:
        for dst in tiles[src]["adjacent"]:
            owner = tiles[dst].get("owner")
            if owner in (me, None):
                continue
            cands.append((int(tiles[dst].get("troops") or 0), src, dst, owner))
    if me == "blue":
        cands.sort(key=lambda c: (0 if c[3] == "neutral" else 1, c[0], c[1]))
    else:
        cands.sort(key=lambda c: (c[0], c[1], c[2]))

    attacks = []
    spent = {n: 0 for n in mine}
    max_attacks = int(view.get("max_attacks") or 3)
    for dtroops, src, dst, owner in cands:
        if len(attacks) >= max_attacks:
            break
        spare = troops[src] - 1 - spent[src]
        if spare < 1 or spare < dtroops:
            continue
        commit = min(spare, dtroops)
        attacks.append({
            "from": src,
            "to": dst,
            "troops": commit,
            "battle_cry": f"{me} claims {dst}",
        })
        spent[src] += commit

    others = [f for f in (view.get("alive") or []) if f != me]
    messages = []
    if others and int(view.get("turn") or 0) == 1:
        messages.append({"to": others[0], "text": "No pact. I take what I can hold."})
    held = ", ".join(mine)
    return {
        "reinforce": reinforce,
        "attacks": attacks,
        "fortify": None,
        "spy": None,
        "messages": messages,
        "memory": f"turn {view.get('turn')}: hold {held}; stacked {host}; swings {len(attacks)}",
    }


class HeuristicCohort:
    """In-process controller. ``__call__`` matches FileCohort and SubprocessCohort."""

    def __call__(self, req: dict) -> dict:
        if req.get("kind") == "duel":
            return solve_duel(req.get("prompt") or "")
        return plan_orders(req)

    def close(self) -> None:
        return None


def main() -> None:
    bot = HeuristicCohort()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({"id": None, "body": {}}) + "\n")
            sys.stdout.flush()
            continue
        try:
            body = bot(req)
        except Exception as exc:  # a bad turn forfeits; the process stays up
            print(f"cohort bot error: {exc}", file=sys.stderr)
            body = {}
        sys.stdout.write(json.dumps({"id": req.get("id"), "body": body}) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
