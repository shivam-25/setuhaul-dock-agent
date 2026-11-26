"""Slides for the explainer video.

Five cards, in the order a reviewer asks the questions: what was wrong, what was
built, how it is put together, how a message moves through it, and where a language
model is allowed to touch any of it.

Drawn rather than screenshotted so the wording stays in version control and a change
to the architecture is a diff here, not a new screenshot someone has to remember to
retake.
"""
from PIL import Image, ImageDraw, ImageFont
import pathlib

W, H = 1280, 720
INK, PAPER, MUTED = (17, 19, 24), (233, 234, 238), (153, 161, 174)
AMBER, GREEN, STOP, LINE = (217, 121, 4), (74, 195, 164), (240, 138, 126), (54, 61, 74)
SLATE = (30, 36, 46)
F = "/System/Library/Fonts/Supplemental/"
HEAD = lambda s: ImageFont.truetype(F + "Arial Bold.ttf", s)
BODY = lambda s: ImageFont.truetype(F + "Arial.ttf", s)
MONO = lambda s: ImageFont.truetype(F + "Courier New Bold.ttf", s)
OUT = pathlib.Path(__file__).resolve().parents[1] / "build" / "explainer" / "cards"
OUT.mkdir(parents=True, exist_ok=True)


def wrap(d, text, font, maxw):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=font) <= maxw: cur = t
        else: lines.append(cur); cur = w
    if cur: lines.append(cur)
    return lines


def chrome(d, eyebrow, headline, accent):
    """Every card wears the same hat, so the eye stops moving between slides."""
    d.rectangle([0, 0, W, 4], fill=accent)
    d.text((72, 60), eyebrow.upper(), font=MONO(16), fill=MUTED)
    y = 100
    for ln in wrap(d, headline, HEAD(50), W - 144):
        d.text((72, y), ln, font=HEAD(50), fill=PAPER); y += 60
    return y + 10


def node(d, x, y, w, h, lines, accent=None, fill=SLATE, title_color=PAPER):
    d.rounded_rectangle([x, y, x + w, y + h], 8, fill=fill,
                        outline=accent or LINE, width=2 if accent else 1)
    ty = y + (h - (len(lines) * 24 + 6)) // 2
    for i, ln in enumerate(lines):
        f = HEAD(19) if i == 0 else BODY(16)
        d.text((x + w // 2 - d.textlength(ln, font=f) // 2, ty),
               ln, font=f, fill=title_color if i == 0 else MUTED)
        ty += 26 if i == 0 else 22


def arrow(d, x1, y, x2, color=MUTED, label=None, dashed=False):
    if dashed:
        for sx in range(int(x1), int(x2) - 8, 12):
            d.line([sx, y, min(sx + 6, x2 - 8), y], fill=color, width=2)
    else:
        d.line([x1, y, x2 - 8, y], fill=color, width=2)
    d.polygon([(x2, y), (x2 - 10, y - 5), (x2 - 10, y + 5)], fill=color)
    if label:
        d.text(((x1 + x2) // 2 - d.textlength(label, font=MONO(13)) // 2, y - 22),
               label, font=MONO(13), fill=MUTED)


def varrow(d, x, y1, y2, color=MUTED, label=None, dashed=True):
    if dashed:
        for sy in range(int(y1), int(y2) - 8, 12):
            d.line([x, sy, x, min(sy + 6, y2 - 8)], fill=color, width=2)
    else:
        d.line([x, y1, x, y2 - 8], fill=color, width=2)
    d.polygon([(x, y2), (x - 5, y2 - 10), (x + 5, y2 - 10)], fill=color)
    if label:
        d.text((x + 14, (y1 + y2) // 2 - 9), label, font=MONO(13), fill=MUTED)


def save(im, idx):
    im.save(OUT / f"{idx:02d}.png"); print("card", idx)


# ---------------------------------------------------------------- 1. the problem
im = Image.new("RGB", (W, H), INK); d = ImageDraw.Draw(im)
y = chrome(d, "SetuHaul freight  ·  the dock exception problem",
           "Two drivers, one dock door", STOP)
for ln in wrap(d, "A freight plan is a chain of promises: a truck, a load, an arrival "
                  "time, a dock, and a customer waiting for stock. One late truck breaks "
                  "several at once, and rain breaks thirty-five of them inside half an hour.",
               BODY(26), W - 170):
    d.text((72, y), ln, font=BODY(26), fill=MUTED); y += 38
y += 26
for big, cap, col in [("35", "drivers messaging in the same half hour", PAPER),
                      ("32", "dock doors across six facilities", PAPER),
                      ("0", "live GPS feeds — every ETA is a claim", STOP)]:
    d.text((72, y), big, font=HEAD(60), fill=col)
    d.text((190, y + 20), cap, font=BODY(24), fill=MUTED); y += 86
d.text((72, 640), "Two conversations read the same free slot. Both drivers say yes. "
                  "Only one promise can be true.", font=BODY(23), fill=STOP)
save(im, 1)

# --------------------------------------------------------------- 2. the solution
im = Image.new("RGB", (W, H), INK); d = ImageDraw.Draw(im)
y = chrome(d, "what was built", "The model proposes. The database decides.", AMBER)
for ln in wrap(d, "A dock-exception service drivers message in plain language. It reads "
                  "the message, works out what the truck can actually reach, holds one "
                  "slot, and tells a human to accept it.", BODY(26), W - 170):
    d.text((72, y), ln, font=BODY(26), fill=MUTED); y += 38
y += 18
for b in ["Capacity is arithmetic, not judgement — ten predicates in code decide what is "
          "feasible, never a model.",
          "Nothing is promised until a warehouse accepts. Options are offered unreserved; "
          "only an explicit yes takes a hold.",
          "Two partial unique indexes make a double booking impossible at the database, "
          "not merely unlikely in the code.",
          "Every decision is written to an append-only log, so any answer can be "
          "reconstructed from what it saw."]:
    d.rectangle([72, y + 12, 86, y + 16], fill=AMBER)
    for j, ln in enumerate(wrap(d, b, BODY(24), W - 230)):
        d.text((104, y), ln, font=BODY(24), fill=PAPER if j == 0 else MUTED); y += 33
    y += 14
save(im, 2)

# ----------------------------------------------------------- 3. the architecture
im = Image.new("RGB", (W, H), INK); d = ImageDraw.Draw(im)
chrome(d, "solution architecture", "Three roles, one engine, one database", AMBER)
for i, (r, sub) in enumerate([("Driver", "own trips only"), ("Coordinator", "all facilities"),
                              ("Warehouse", "one facility")]):
    node(d, 60, 232 + i * 76, 170, 62, [r, sub])
d.line([230, 263, 262, 263], fill=MUTED, width=2)
d.line([230, 339, 262, 339], fill=MUTED, width=2)
d.line([230, 415, 262, 415], fill=MUTED, width=2)
d.line([262, 263, 262, 415], fill=MUTED, width=2)
arrow(d, 262, 339, 320, label="cookie")
node(d, 320, 272, 206, 134, ["Next.js · Vercel", "", "the three consoles",
                             "/api/* proxied to", "the same origin"])
arrow(d, 526, 339, 596, label="HttpOnly")
node(d, 596, 272, 212, 134, ["AWS Lambda", "ap-southeast-2", "", "FastAPI + the",
                             "eleven-step engine"], accent=AMBER)
arrow(d, 808, 339, 878, label="SQL")
node(d, 878, 272, 212, 134, ["Postgres", "ap-south-1", "", "2 partial unique",
                             "indexes · audit log"], accent=GREEN)
varrow(d, 702, 406, 470, label="2 of 11 steps")
node(d, 596, 470, 212, 78, ["Amazon Bedrock", "nova-micro, APAC"], accent=AMBER)
d.text((72, 596), "The execution role allows amazon.nova-* and nothing else. The engine "
                  "never trusts the browser for identity:", font=BODY(21), fill=MUTED)
d.text((72, 626), "every request is re-checked against the session, and every query is "
                  "scoped to the role that asked.", font=BODY(21), fill=MUTED)
save(im, 3)

# -------------------------------------------------------------- 4. the data flow
im = Image.new("RGB", (W, H), INK); d = ImageDraw.Draw(im)
chrome(d, "data flow  ·  one message, eleven steps", "What happens to \"I'm 2 hours late\"",
       AMBER)
steps = [("1", "load", "session, trip,\nsnapshot"), ("2", "screen", "injection\nrefused ~1ms"),
         ("3", "dedupe", "retry inside\n120s replayed"), ("4", "extract", "LLM reads\nthe text"),
         ("5", "resolve", "which trip,\nwhich option"), ("6", "eta", "+ confidence\nbuffer"),
         ("7", "route", "book, status,\nor ask"), ("8", "prefer", "10 predicates\nrank slots"),
         ("9", "hold", "INSERT hits\nthe index"), ("10", "commit", "output checks\nor escalate"),
         ("11", "reply", "LLM writes\nit — or code")]
bw, bh, gap = 276, 106, 18
ROWS = [(0, 4), (4, 8), (8, 11)]            # 4 / 4 / 3, so nothing runs off the edge
for ri, (a, b) in enumerate(ROWS):
    count = b - a
    x0 = (W - (count * bw + (count - 1) * gap)) // 2
    yy = 200 + ri * (bh + 26)
    for ci in range(count):
        n, name, sub = steps[a + ci]
        x = x0 + ci * (bw + gap)
        llm = name in ("extract", "reply")
        acc = AMBER if llm else (GREEN if name in ("hold", "screen") else None)
        d.rounded_rectangle([x, yy, x + bw, yy + bh], 8, fill=SLATE,
                            outline=acc or LINE, width=2 if acc else 1)
        d.text((x + 14, yy + 11), n, font=MONO(15), fill=acc or MUTED)
        d.text((x + 42, yy + 9), name, font=HEAD(22), fill=PAPER)
        for j, ln in enumerate(sub.split("\n")):
            d.text((x + 14, yy + 46 + j * 24), ln, font=BODY(18), fill=MUTED)
        if ci < count - 1:
            arrow(d, x + bw + 2, yy + bh // 2, x + bw + gap - 2)
d.rectangle([150, 604, 164, 608], fill=AMBER)
d.text((176, 592), "a language model runs here \u2014 and only here", font=BODY(22), fill=PAPER)
d.rectangle([700, 604, 714, 608], fill=GREEN)
d.text((726, 592), "the database decides here, not the code", font=BODY(22), fill=PAPER)
d.text((150, 652), "Any step can end the message early. An injection never reaches step 4; "
                  "a status question never reaches step 9.", font=BODY(21), fill=MUTED)
save(im, 4)

# ------------------------------------------------------- 5. where the model sits
im = Image.new("RGB", (W, H), INK); d = ImageDraw.Draw(im)
y = chrome(d, "where the LLM is used, and how", "Two jobs, both at the edges", AMBER)
for ln in wrap(d, "Capacity does not need a language model; it needs arithmetic. The model "
                  "earns its place only where the work starts and ends in messy human "
                  "sentences.", BODY(25), W - 170):
    d.text((72, y), ln, font=BODY(25), fill=MUTED); y += 36
y += 20
for ttl, sub, body in [
    ("IN  ·  step 4", "nova-micro, temperature 0, JSON only",
     "Turns \"around two hours late, anything after 7\" into fields: an intent, a declared "
     "ETA, a window. It is given no slot data and no booking power — it cannot schedule "
     "anything, only describe what was said."),
    ("OUT  ·  step 11", "nova-micro, or no model at all",
     "Phrases the decision that code already made. The slots, times and refusals are passed "
     "in; it may not invent one. Output checks compare every time it prints against the "
     "decision, and a mismatch is escalated to a human instead of sent."),
]:
    d.rounded_rectangle([72, y, W - 72, y + 150], 10, fill=SLATE, outline=AMBER, width=2)
    d.text((96, y + 18), ttl, font=MONO(17), fill=AMBER)
    d.text((96 + d.textlength(ttl, font=MONO(17)) + 22, y + 19), sub, font=BODY(18), fill=MUTED)
    yy = y + 52
    for ln in wrap(d, body, BODY(22), W - 220):
        d.text((96, yy), ln, font=BODY(22), fill=PAPER); yy += 31
    y += 172
d.text((72, y + 6), "Everything between them — feasibility, ranking, the booking, the "
                    "scope checks — runs without a model.", font=BODY(22), fill=GREEN)
save(im, 5)
