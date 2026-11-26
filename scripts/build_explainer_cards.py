from PIL import Image, ImageDraw, ImageFont
import pathlib

W, H = 1280, 720
INK, PAPER, MUTED = (17, 19, 24), (233, 234, 238), (153, 161, 174)
AMBER, GREEN, STOP, LINE = (217, 121, 4), (74, 195, 164), (240, 138, 126), (43, 48, 58)
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

def card(idx, eyebrow, headline, body=None, stats=None, bullets=None, accent=AMBER):
    im = Image.new("RGB", (W, H), INK); d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 4], fill=accent)
    y = 68
    d.text((72, y), eyebrow.upper(), font=MONO(16), fill=MUTED); y += 42
    for ln in wrap(d, headline, HEAD(54), W - 144):
        d.text((72, y), ln, font=HEAD(54), fill=PAPER); y += 64
    y += 16
    if body:
        for ln in wrap(d, body, BODY(27), W - 170):
            d.text((72, y), ln, font=BODY(27), fill=MUTED); y += 39
    if bullets:
        y += 10
        for b in bullets:
            d.rectangle([72, y + 13, 86, y + 17], fill=accent)
            for j, ln in enumerate(wrap(d, b, BODY(26), W - 230)):
                d.text((104, y), ln, font=BODY(26), fill=PAPER if j == 0 else MUTED)
                y += 36
            y += 12
    if stats:
        y = max(y + 26, 430)
        colw = (W - 144) // len(stats)
        for i, (big, cap) in enumerate(stats):
            x = 72 + i * colw
            d.text((x, y), big, font=HEAD(72), fill=accent)
            for j, ln in enumerate(wrap(d, cap, BODY(21), colw - 34)):
                d.text((x, y + 90 + j * 28), ln, font=BODY(21), fill=MUTED)
    im.save(OUT / f"{idx:02d}.png"); print("card", idx)

card(1, "SetuHaul freight  ·  dock exception service", "Two promises, one dock door",
     "Thirty-five drivers message inside half an hour. Six facilities, thirty-two dock "
     "doors, and not one live GPS feed. Every late truck needs another slot, and no slot "
     "can be given away twice.")

card(2, "the rule the whole design rests on", "The model proposes. The database decides.",
     "Eleven steps run for every message. Exactly two of them touch a language model: "
     "reading the driver's text, and writing the reply. Whether a slot is feasible, which "
     "driver gets it, and the booking itself are never a model's call.")

card(3, "feasibility, and how far a proof goes", "An oracle proves agreement, not correctness",
     "Ten predicates decide feasibility, all of them in code, cross-checked against a "
     "separately written SQL oracle.",
     stats=[("1.0000", "precision across 72 cases"), ("1.0000", "recall"),
            ("0", "defects it caught")], accent=GREEN)

card(4, "the scarce thing is not throughput", "Contention",
     "At peak this is one message a minute. What is scarce is a compatible dock at a given "
     "hour. So twenty threads reach for one dock door, ten times over.",
     stats=[("10/10", "runs with exactly one booking"), ("0", "errors shown to a driver"),
            ("0", "promises made then withdrawn")], accent=GREEN)

card(5, "what using it found that testing did not", "Five bugs the tests passed",
     bullets=["A window that was open, but that the truck could not reach",
              "A chat default that locked out fourteen of fifteen drivers",
              "Three endpoints that would answer about any driver's load",
              "“The second one works” — and the first one was booked",
              "A replay cache keyed on the day, answering 4 PM with the 10 AM slots"],
     accent=STOP)

card(6, "deployed, and measured rather than budgeted", "4.4 seconds, and where they go",
     "Lambda behind a role scoped to Nova only, the interface on Vercel, the state in "
     "Postgres. An organisation policy forbids Lambda in the database's own region, so "
     "most of the turn is distance.",
     stats=[("3.4 s", "network, across two AWS regions"), ("1.1 s", "the model"),
            ("1 ms", "a prompt injection, refused")])
