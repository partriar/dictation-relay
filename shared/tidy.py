"""
Text tidy-up applied on the laptop before pasting (the phone model outputs
spoken-form text: "fourteen", "ten thirty", no final full stop).

- Numbers to digits, Handy-style: 10 and up always; 1-9 only next to other digits
  or after "unit/lot/level/number/no/room/bed/bath/car" ("Unit 4"), else stay words ("one question").
- "one point two million" -> "1.2 million" (scale words kept as words after a decimal)
- times: "at ten thirty" -> "at 10:30"
- "99 M" -> "99M" (digit + lone capital letter, e.g. Tc-99m, Unit 4B)
- big numbers get commas: 620000 -> 620,000
- final punctuation: "?" if the last sentence starts like a question, else "."
"""
import re

try:
    from text_to_num import alpha2digit      # laptop: fast, well-tested library
except ImportError:                           # phone (Termux can't build it): built-in fallback
    alpha2digit = None

_UNITS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if i >= 2}
_SCALES = {"hundred": 100, "thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000}
_ORD = {"first": (1, "st"), "second": (2, "nd"), "third": (3, "rd"), "fifth": (5, "th"), "eighth": (8, "th"),
        "ninth": (9, "th"), "twelfth": (12, "th")}
_DIGIT_WORDS = set(_UNITS) | set(_TENS)


_DIGIT_RUN = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine)(?:\s+(?:zero|one|two|three|four|five|six|seven|eight|nine)\b){2,}",
    re.I)


def _alpha2digit_lite(text: str, threshold: int = 10) -> str:
    """Pure-Python stand-in for text_to_num.alpha2digit (English, common cases)."""
    # Digit-by-digit runs ("zero four one two ...") are read as digits, not as one number.
    text = _DIGIT_RUN.sub(lambda m: " ".join(str(_UNITS[w.lower()]) for w in m.group(0).split()), text)
    text = re.sub(r"\ba (hundred|thousand|million)\b", r"one \1", text, flags=re.I)
    tokens = re.findall(r"[A-Za-z]+(?:-[A-Za-z]+)?|\d+|[^A-Za-z\d]+", text)
    out, i = [], 0
    while i < len(tokens):
        j, total, cur, used, digits_run = i, 0, 0, [], []
        decimal = None
        suffix = ""
        while j < len(tokens):
            tok = tokens[j]
            low = tok.lower()
            parts = low.split("-")
            if tok.strip() == "" and used:            # whitespace between number words
                j += 1
                continue
            if low == "and" and used and j + 2 < len(tokens) and tokens[j + 2].lower().split("-")[0] in _DIGIT_WORDS:
                j += 1
                continue
            if low == "point" and used and j + 2 < len(tokens) and tokens[j + 2].lower() in _UNITS:
                decimal = ""
                j += 1
                continue
            if decimal is not None:
                if low in _UNITS and _UNITS[low] < 10:
                    decimal += str(_UNITS[low])
                    used.append(j)
                    j += 1
                    continue
                break
            if all(p in _UNITS or p in _TENS for p in parts) and len(parts) <= 2:
                v = sum(_UNITS.get(p, _TENS.get(p, 0)) for p in parts)
                # "one two three" (digit by digit) vs "twenty one" (compound)
                if used and cur % 10 == 0 and cur % 100 != 0 and v < 10 and cur >= 20:
                    cur += v
                elif used and (cur != 0 or total != 0) and not (cur % 100 == 0 and cur != 0 and v < 100) and not (cur == 0 and total):
                    digits_run.append(total + cur)
                    total, cur = 0, v
                else:
                    cur += v
                used.append(j)
                j += 1
                continue
            if low in _ORD and (used or True):
                v, sfx = _ORD[low]
                if used and cur % 10 == 0 and cur >= 20:
                    cur += v
                    suffix = sfx
                    used.append(j)
                    j += 1
                break
            if low in _SCALES and used:
                sc = _SCALES[low]
                if sc == 100:
                    cur = (cur or 1) * 100
                else:
                    total += (cur or 1) * sc
                    cur = 0
                used.append(j)
                j += 1
                continue
            break
        if not used:
            out.append(tokens[i])
            i += 1
            continue
        last = used[-1]
        nums = digits_run + [total + cur]
        value = " ".join(str(n) for n in nums)
        single_small = len(nums) == 1 and nums[0] < threshold and decimal is None and not suffix
        if single_small:
            out.append("".join(tokens[i:last + 1]))
        else:
            if decimal:
                value += "." + decimal
            out.append(value + suffix)
        i = last + 1
    return "".join(out)

QUESTION_START = re.compile(
    r"^(can|could|would|will|do|does|did|is|are|was|were|have|has|what|when|where|who|why|how|should|shall|may)\b",
    re.I)
UNIT_WORDS = r"(?:unit|units|lot|level|number|no|room|apartment|flat|shop|suite|stage|step|page|version)"
SMALL = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
         "six": "6", "seven": "7", "eight": "8", "nine": "9"}


def tidy(text: str) -> str:
    t = text.strip()
    if not t:
        return t

    # Keep scale words as words when they follow a decimal ("one point two million").
    keep = {}

    def protect(m):
        key = f"QQK{len(keep)}QQ"
        keep[key] = m.group(2)
        return f"{m.group(1)} {key}"
    t = re.sub(r"\b(point \w+) (million|billion|thousand)\b", protect, t, flags=re.I)

    # Spoken "oh" inside digit strings is zero ("oh four one two ...").
    t = re.sub(r"\boh(?= (?:zero|one|two|three|four|five|six|seven|eight|nine)\b)", "zero", t, flags=re.I)

    t = alpha2digit(t, "en", threshold=10) if alpha2digit else _alpha2digit_lite(t, 10)
    for k, v in keep.items():
        t = t.replace(k, v)

    # Runs of 8+ single digits = a phone number: "0 4 1 2 3 4 5 6 7 8" -> "0412 345 678"
    def phone(m):
        d = m.group(0).replace(" ", "")
        if len(d) == 10 and d.startswith("04"):
            return f"{d[:4]} {d[4:7]} {d[7:]}"
        if len(d) == 10 and d.startswith("0"):
            return f"({d[:2]}) {d[2:6]} {d[6:]}"
        return d
    t = re.sub(r"\b0\d*(?: \d+)+\b",
               lambda m: phone(m) if len(m.group(0).replace(" ", "")) == 10 else m.group(0), t)
    t = re.sub(r"\b\d(?: \d){7,}\b", phone, t)

    # Years after a month: "March 20 27" -> "March 2027"
    t = re.sub(r"\b(January|February|March|April|May|June|July|August|September|October|November|December) (20|19) (\d{2})\b",
               r"\1 \2\3", t)

    # Small numbers before property words: "three bedroom" -> "3 bedroom"
    t = re.sub(r"\b(one|two|three|four|five|six|seven|eight|nine)(?=[ -](?:bed|beds|bedroom|bedrooms|bath|baths|bathroom|bathrooms|car|cars|storey|story|level)\b)",
               lambda m: SMALL[m.group(1).lower()], t, flags=re.I)

    # Small numbers after unit-like words: "Unit Four" -> "Unit 4"
    t = re.sub(rf"\b({UNIT_WORDS}) (one|two|three|four|five|six|seven|eight|nine)\b",
               lambda m: f"{m.group(1)} {SMALL[m.group(2).lower()]}", t, flags=re.I)

    # Times: "at 10 30" -> "at 10:30" (only after time-ish words, minutes 00-59)
    t = re.sub(r"\b(at|from|until|till|til|after|before|by|around|about) (\d{1,2}) ([0-5]\d)\b",
               lambda m: f"{m.group(1)} {m.group(2)}:{m.group(3)}" if int(m.group(2)) <= 23 else m.group(0),
               t, flags=re.I)

    # "99 M" -> "99M", "4 B" -> "4B" (lone capital letter after a number; not "I"/"A")
    t = re.sub(r"\b(\d+) ([B-HJ-Z])\b", r"\1\2", t)

    # Commas in big plain integers (not years 1900-2099, not with leading zeros / phone numbers)
    def commas(m):
        s = m.group(0)
        if len(s) < 5 or s.startswith("0"):
            return s
        n = int(s)
        if n >= 1_000_000 and n % 100_000 == 0:          # 2000000 -> "2 million", 1500000 -> "1.5 million"
            return f"{n / 1_000_000:g} million"
        return f"{n:,}"
    t = re.sub(r"(?<![\d.,:])\d+(?![\d.,:])", commas, t)

    # Final punctuation
    if t[-1].isalnum():
        last = re.split(r"(?<=[.!?])\s+", t)[-1]
        t += "?" if QUESTION_START.match(last) else "."

    return t[0].upper() + t[1:]


if __name__ == "__main__":
    tests = [
        "Inspection at Fourteen Elm Street Riverdale is booked for Saturday at ten thirty",
        "The vendor in Harbour Point once offers above one point two million by Friday",
        "Please call me back after two. I'm at an appraisal in Northfield until then",
        "Ticnithinium ninety nine M is the most common tracer used in bone skidna free",
        "I spoke with the buyers this morning and they're keen. Can you send me the strata records for Unit Four and let the owners know we'll have an answer by Thursday afternoon",
        "I have one question and a couple of ideas, call me at nine",
        "It's a three bedroom two bathroom house on six hundred and twenty square metres",
        "The price guide is six hundred and fifty thousand to seven hundred thousand",
        "Settlement is on the twenty first of March twenty twenty seven",
        "My number is oh four one two three four five six seven eight",
        "Is the open home still at eleven fifteen",
        "Can you send me the strata records for Unit Four",
        "Price is one point five million or two million",
        "Call me on oh seven three three six nine one two three four",
        "a",
    ]
    for x in tests:
        print(" ", tidy(x))
