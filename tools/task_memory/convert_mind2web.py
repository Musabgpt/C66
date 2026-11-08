"""Mind2Web train split (OSU NLP, CC BY 4.0) -> unified task-memory procedures for the phone's Chrome.

Input: one JSON line per task with website, domain, subdomain, confirmed_task and action_reprs, read from the Hugging
Face dataset osunlp/Mind2Web (train split only: the test splits are password-protected by their authors to keep them
out of training data, so they are not used). Each "[button]  Search -> CLICK" becomes a tap on "Search",
"[textbox] From -> TYPE: Paris" a tap on the field then typing, "[combobox] Month -> SELECT: May" a tap on the menu
then on the choice. Usage: python3 convert_mind2web.py <compact.jsonl> <out.jsonl>
"""
import json
import re
import sys

REPR = re.compile(r"^\[(?P<role>[^\]]*)\]\s*(?P<label>.*?)\s*->\s*(?P<op>CLICK|TYPE|SELECT|HOVER|ENTER)(?::\s*(?P<value>.*))?$",
                  re.S)
SITES = {
    "aa": "www.aa.com", "qatar": "www.qatarairways.com", "us.megabus": "us.megabus.com",
    "new.mta.info": "new.mta.info", "mbta": "www.mbta.com", "amtrak": "www.amtrak.com",
    "united": "www.united.com", "delta": "www.delta.com", "jetblue": "www.jetblue.com",
    "ryanair": "www.ryanair.com", "kayak": "www.kayak.com", "expedia": "www.expedia.com",
    "booking": "www.booking.com", "exploretock": "www.exploretock.com", "resy": "resy.com",
    "yelp": "www.yelp.com", "opentable": "www.opentable.com", "carmax": "www.carmax.com",
    "cars": "www.cars.com", "budget": "www.budget.com", "enterprise": "www.enterprise.com",
    "rentalcars": "www.rentalcars.com", "kohls": "www.kohls.com", "amazon": "www.amazon.com",
    "target": "www.target.com", "ikea": "www.ikea.com", "rei": "www.rei.com", "gamestop": "www.gamestop.com",
    "instacart": "www.instacart.com", "nfl": "www.nfl.com", "espn": "www.espn.com", "imdb": "www.imdb.com",
    "rottentomatoes": "www.rottentomatoes.com", "spothero": "spothero.com", "seatgeek": "seatgeek.com",
    "ticketcenter": "www.ticketcenter.com", "tvguide": "www.tvguide.com", "soundcloud": "soundcloud.com",
    "last.fm": "www.last.fm", "discogs": "www.discogs.com", "uniqlo": "www.uniqlo.com", "carnival": "www.carnival.com",
    "redbox": "www.redbox.com", "nyc": "www.nyc.gov", "koa": "koa.com", "foxsports": "www.foxsports.com", "cvs": "www.cvs.com", "underarmour":
    "www.underarmour.com", "marriott": "www.marriott.com", "airbnb": "www.airbnb.com", "eventbrite": "www.eventbrite.com",
}


def site_url(website):
    w = str(website or "").strip().lower()
    host = SITES.get(w)
    if not host:
        if "." in w:
            host = w if w.rsplit(".", 1)[-1] in ("com", "org", "net", "gov", "info", "fm", "io", "edu") else w + ".com"
        else:
            host = "www.%s.com" % w
    return "https://" + host


def clean(label):
    label = re.sub(r"\s+", " ", str(label or "")).strip()
    return label.rstrip(".").strip() if label.endswith("...") else label


def convert(src, out_path):
    n = n_exec = 0
    with open(src, encoding="utf-8") as f, open(out_path, "w", encoding="utf-8") as out:
        for line in f:
            d = json.loads(line)
            task = re.sub(r"\s+", " ", d["task"]).strip()
            steps = [{"a": "open_url", "url": site_url(d["website"])}]
            slots, executable = [], True
            for rep in d.get("action_reprs") or []:
                m = REPR.match(rep.strip())
                if not m:
                    executable = False
                    steps.append({"a": "note", "hint": rep[:200]})
                    continue
                role, label, op = m.group("role").strip(), clean(m.group("label")), m.group("op")
                value = (m.group("value") or "").strip()
                tap = {"a": "tap", "role": role}
                if label:
                    tap["t"] = [label[:120]]
                else:
                    executable = False
                    tap["hint"] = "unnamed %s" % role
                if op in ("CLICK", "HOVER", "ENTER"):
                    steps.append(tap)
                elif op == "TYPE":
                    tap["field"] = True
                    steps.append(tap)
                    text = value
                    if text and text.lower() in task.lower():
                        if text not in slots:
                            slots.append(text)
                        text = "{%d}" % (slots.index(text) + 1)
                    steps.append({"a": "type", "text": text})
                elif op == "SELECT":
                    steps.append(tap)
                    steps.append({"a": "tap", "t": [value[:120]], "role": "option"})
            pattern = task
            for i, v in enumerate(slots):
                pattern = re.sub(re.escape(v), "{%d}" % (i + 1), pattern, flags=re.I)
            rec = {"key": "m2w.%s" % d["annotation_id"][:8], "src": "mind2web", "plat": "web",
                   "app": d["website"], "pkg": site_url(d["website"])[8:], "goal": task, "steps": steps,
                   "exec": bool(executable), "slots": slots, "start": "url",
                   "domain": d.get("domain", ""), "subdomain": d.get("subdomain", "")}
            if slots:
                rec["pattern"] = pattern
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
            n_exec += executable
    print("mind2web tasks", n, "executable", n_exec)


if __name__ == "__main__":
    convert(sys.argv[1], sys.argv[2])
