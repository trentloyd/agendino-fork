#!/usr/bin/env python3
"""
Export Agendino 1:1 recordings into an Obsidian "Team Manager" structure.

Reads the Agendino SQLite DB read-only and generates:
  Team Manager/People/<Name>.md      (type: person, role, relation)
  Team Manager/Meetings/<YYYY-MM-DD Name>.md  (type: meeting, person, date)

Original Agendino/ notes and DB are never modified.

Usage:
  python export_team_manager.py preview   # People notes + 3 sample meetings
  python export_team_manager.py full      # everything
"""
import sqlite3, re, sys, os
from collections import defaultdict, OrderedDict

DB = "/opt/agendino/settings/agendino.db"
VAULT = "/home/git/obsidian-working"
PEOPLE_DIR = os.path.join(VAULT, "Team Manager", "People")
MEET_DIR = os.path.join(VAULT, "Team Manager", "Meetings")

ME = "Trent"

# ---- person detection -------------------------------------------------------
# Suffixes that mark a person meeting (1:1 / recurring / intro / one-off).
TYPE_RE = r"(Weekly|Bi[-\s]?Weekly|Biweekly|Monthly|One[-\s]?Off|Intro|Travel|MPU)"
# Leading tokens that are teams/projects, never people.
NONPERSON = {
    "CTV", "All", "Distribution", "DART", "CTVDart", "CTVDART", "Nova", "PVC",
    "Amazon", "Design", "Streaming", "Engagement", "DMW", "Live", "Testing",
    "Biweekly", "BiWeekly", "Bi", "Daily", "CTVMM", "PSDK", "FVR", "Monitor",
    "MT", "International", "App", "LG", "Coffee", "Project", "Continuous",
    "ISpot", "iSpot", "Episodic", "Growth", "Content", "Resource", "Business",
    "Addressing", "Finalizing", "Candidate", "Hiring", "Leadership", "Personal",
    "Organizational", "Team", "Work", "Accessibility", "Mission", "April",
    "2026", "Frank",  # Frank = interview candidate, not a report
}
# Recording-name substrings that are group meetings even if they start w/ a name.
GROUP_SUBSTR = ["Directs"]  # e.g. "Anna Directs - Bi-Weekly"

# Canonical merge for name variants -> canonical display name.
ALIAS = {
    "Ashley N.": "Ashley",   # same person as "Ashley"
    "Ashley N": "Ashley",
}

# Inferred role/relation. relation in {manager, peer, team, other}.
# GUESSES from meeting context -- correct as needed.
ROLE = {
    "Anna":     ("VP of Product",                 "manager"),
    "Ashley":   ("Head of Product & Data",        "manager"),  # Anna's manager (skip-level)
    "Rob":      ("Chief of Staff",                 "peer"),
    "Steve":    ("TPM Lead",                       "peer"),
    "Steph":    ("Product Lead",                   "peer"),
    "Andrew":   ("Director of Engineering, CTV",   "peer"),
    "Manav":    ("Events & Visuals",               "peer"),
    "Han":      ("Mobile App",                     "peer"),
    "Charles":  ("Director of Fintech",            "peer"),   # internal colleague, cross-functional
    "Will":     ("Product Manager",                "peer"),
    "Alex":     ("Product Manager, CTV",           "team"),
    "Ariel":    ("Product Manager",                "team"),
    "Heddy":    ("PM, Media Management",           "team"),
    "Becca":    ("Product Manager (new hire)",     "team"),
    "Liz":      ("Product Manager",                "team"),
    "Odille":   ("Senior Staff PM",                "team"),
    "Alisa":    ("CTV Designer",                   "team"),
    "James":    ("Intro",                          "other"),
    "Joey":     ("Intro",                          "other"),
    "Justin":   ("Intro",                          "other"),
    "Rose":     ("Intro",                          "other"),
    "Lindsay":  ("One-off",                        "other"),
}


def canon_person(name):
    """Return canonical display name for a person 1:1, or None if not a 1:1."""
    base = re.sub(r"^\d{4}-\d\d-\d\d\s*-?\s*", "", name).strip()
    base = re.sub(r"^April \d+, \d{4}\s*-?\s*", "", base).strip()
    for g in GROUP_SUBSTR:
        if g in base:
            return None
    disp = None
    # "Name  Trent" / "Name - Trent" (early format)
    m = re.match(r"^([A-Z][a-z]+)\s\s+Trent$", base) or re.match(r"^([A-Z][a-z]+)\s+-\s+Trent", base)
    if m:
        disp = m.group(1)
    else:
        # "Name [Initial. | Lastname] - <type...>"
        m = re.match(rf"^([A-Z][a-z]+(?:\s+[A-Z]\.| [A-Z][a-z]+)?)\s*-\s*.*?{TYPE_RE}", base, re.I)
        if m:
            disp = m.group(1).strip()
    if not disp:
        return None
    lead = disp.split()[0]
    if lead in NONPERSON or lead == ME:
        return None
    disp = ALIAS.get(disp, disp)
    return disp


def link_name(disp):
    """Wikilink target (no trailing dot)."""
    return disp.rstrip(".")


# ---- summary parsing --------------------------------------------------------
def section(txt, *titles):
    """Return the body text under the first matching '#'-heading, until next heading."""
    for t in titles:
        m = re.search(rf"^#{{1,4}}\s*{re.escape(t)}\s*$", txt, re.M | re.I)
        if not m:
            continue
        seg = txt[m.end():]
        nx = re.search(r"^#{1,4}\s", seg, re.M)
        return seg[:nx.start()] if nx else seg
    return None


def bullets(seg, limit=None):
    """Extract top-level bullet lines from a section body."""
    if not seg:
        return []
    out = []
    for ln in seg.splitlines():
        s = ln.strip()
        m = re.match(r"^[-*]\s+(.*)", s)
        if m and m.group(1).strip():
            out.append(m.group(1).strip())
    return out[:limit] if limit else out


DUE_SKIP = {"", "date missing", "missing", "not specified", "n/a", "tbd", "none", "-", "ongoing"}


def sub_speakers(text, person):
    """Speaker 1 -> Trent, Speaker 2 -> the person, in any body text."""
    if not text:
        return text
    text = re.sub(r"\bSpeaker\s*1\b", ME, text)
    text = re.sub(r"\bSpeaker\s*2\b", person, text)
    return text


def resolve_owner(owner, person):
    if not owner:
        return "Unassigned"
    o = sub_speakers(owner.strip(), person)
    o = re.sub(r"(?i)\bowner missing\b", "Unassigned", o)
    o = re.sub(r"\s*\((?:Trent|" + re.escape(person) + r")\)\s*$", "", o)
    return o.strip() or "Unassigned"


def parse_action_items(txt, person):
    """Parse the Action Items markdown table into checkbox lines."""
    seg = section(txt, "Action Items", "Action Items / Next Steps")
    if not seg:
        return []
    rows = []
    lines = [l for l in seg.splitlines() if l.strip().startswith("|")]
    if len(lines) >= 2:
        header = [c.strip().lower() for c in lines[0].strip("|").split("|")]

        def col(*names):
            for i, h in enumerate(header):
                if any(n in h for n in names):
                    return i
            return None
        ci_act = col("action", "task")
        ci_own = col("owner", "assigned")
        ci_due = col("due")
        ci_pri = col("priority")
        ci_sta = col("status")
        for ln in lines[2:]:  # skip header + separator
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if not cells or ci_act is None or ci_act >= len(cells):
                continue
            act = sub_speakers(cells[ci_act], person)
            if not act or set(act) <= set("-: "):
                continue
            owner = resolve_owner(cells[ci_own] if ci_own is not None and ci_own < len(cells) else "", person)
            due = cells[ci_due] if ci_due is not None and ci_due < len(cells) else ""
            pri = cells[ci_pri] if ci_pri is not None and ci_pri < len(cells) else ""
            sta = cells[ci_sta] if ci_sta is not None and ci_sta < len(cells) else ""
            done = any(w in sta.lower() for w in ("done", "complete", "closed"))
            box = "x" if done else " "
            extra = []
            if due and due.lower() not in DUE_SKIP:
                extra.append(f"due: {due}")
            if pri and pri.lower() == "high":
                extra.append("priority: high")
            tail = f"  _({'; '.join(extra)})_" if extra else ""
            rows.append(f"- [{box}] **@{owner}** — {act}{tail}")
    else:
        # bullet-style fallback
        for b in bullets(seg):
            rows.append(f"- [ ] {sub_speakers(b, person)}")
    return rows


def discussion_points(txt):
    seg = section(txt, "Executive Summary")
    pts = bullets(seg)
    if pts:
        return pts
    # fallback: Topics Discussed subheadings
    seg = section(txt, "Topics Discussed", "Detailed Summary")
    if seg:
        heads = re.findall(r"^#{3,4}\s+(?:\d+\.\s*)?(.*)$", seg, re.M)
        if heads:
            return [h.strip() for h in heads]
    # last resort: first bullets anywhere
    return bullets(txt, 12)


def opt_section(txt, title, *aliases):
    return bullets(section(txt, title, *aliases))


# ---- rendering --------------------------------------------------------------
def yaml_list(items):
    return "[" + ", ".join(items) + "]"


def render_meeting(person, date, rec_name, summary_row, opts):
    txt = summary_row["summary"]
    tags = [t.strip().replace(" ", "-") for t in (summary_row["tags"] or "").split(",") if t.strip()]
    ln = link_name(person)
    title = summary_row["title"] or f"{person} 1:1"
    out = []
    out.append("---")
    out.append("type: meeting")
    out.append(f'person: "[[{ln}]]"')
    out.append(f"date: {date}")
    if tags:
        out.append(f"tags: {yaml_list(tags)}")
    out.append(f'meeting-title: "{title}"')
    out.append(f'source-recording: "{rec_name}"')
    out.append("---")
    out.append("")
    out.append(f"# {date} — 1:1 with {person}")
    out.append("")
    out.append(f"> Migrated from Agendino recording: `{rec_name}`")
    out.append("")
    sp = lambda xs: [sub_speakers(x, person) for x in xs]
    out.append("## Discussion Points")
    dp = sp(discussion_points(txt))
    out += [f"- {p}" for p in dp] if dp else ["- _(none captured)_"]
    out.append("")
    if opts.get("decisions"):
        d = sp(opt_section(txt, "Decisions", "Decisions Made"))
        if d:
            out.append("## Decisions"); out += [f"- {x}" for x in d]; out.append("")
    if opts.get("risks"):
        r = sp(opt_section(txt, "Risks & Blockers", "Risks and Blockers", "Blockers"))
        if r:
            out.append("## Risks & Blockers"); out += [f"- {x}" for x in r]; out.append("")
    out.append("## Action Items")
    ai = parse_action_items(txt, person)
    out += ai if ai else ["- _(none captured)_"]
    out.append("")
    if opts.get("questions"):
        q = sp(opt_section(txt, "Open Questions", "Follow-ups Needed", "Follow-ups", "Next Steps"))
        if q:
            out.append("## Open Questions & Follow-ups"); out += [f"- {x}" for x in q]; out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_person(person, meetings):
    ln = link_name(person)
    role, rel = ROLE.get(person, ("", "team"))
    out = []
    out.append("---")
    out.append("type: person")
    out.append(f"name: {person}")
    out.append(f'role: "{role}"')
    out.append(f"relation: {rel}")
    out.append("tags: [person, team-manager]")
    out.append("---")
    out.append("")
    out.append(f"# {person}")
    out.append("")
    out.append(f"**Role:** {role or 'TBD'}")
    out.append(f"**Relation:** {rel}")
    out.append(f"**1:1 count:** {len(meetings)}")
    out.append("")
    out.append("## 1:1 Meetings")
    out.append("```dataview")
    out.append('table date as "Date", meeting-title as "Topic"')
    out.append('from "Team Manager/Meetings"')
    out.append(f"where person = [[{ln}]]")
    out.append("sort date desc")
    out.append("```")
    out.append("")
    out.append("## Notes")
    out.append("- ")
    return "\n".join(out) + "\n"


# ---- main -------------------------------------------------------------------
def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "preview"
    opts = {"decisions": True, "risks": True, "questions": True}

    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    recs = c.execute("select id,name,recorded_at from recording order by recorded_at,id").fetchall()

    people = defaultdict(list)   # person -> list of (date, rec_id, rec_name)
    for r in recs:
        p = canon_person(r["name"])
        if p:
            people[p].append(((r["recorded_at"] or "")[:10], r["id"], r["name"]))

    os.makedirs(PEOPLE_DIR, exist_ok=True)
    os.makedirs(MEET_DIR, exist_ok=True)

    # roster
    print(f"=== ROSTER: {len(people)} people, {sum(len(v) for v in people.values())} meetings ===")
    for p in sorted(people):
        role, rel = ROLE.get(p, ("?", "team"))
        print(f"  {p:12} x{len(people[p]):2}  [{rel:7}] {role}")

    # write People notes (always)
    for p in sorted(people):
        path = os.path.join(PEOPLE_DIR, f"{link_name(p)}.md")
        with open(path, "w") as f:
            f.write(render_person(p, people[p]))
    print(f"\nWrote {len(people)} People notes -> Team Manager/People/")

    # collect meeting jobs, sorted by date
    jobs = []
    for p in sorted(people):
        for date, rid, rname in people[p]:
            jobs.append((date, p, rid, rname))
    jobs.sort(key=lambda x: (x[0], x[1]))

    if mode == "preview":
        # 3 representative recent, well-structured meetings
        picks = [235, 227, 230]  # Alex, Heddy, Ariel (July, full structure)
        jobs = [j for j in jobs if j[2] in picks]

    seen = {}
    written = 0
    for date, p, rid, rname in jobs:
        s = c.execute("select * from summary where recording_id=? order by version desc limit 1", (rid,)).fetchone()
        if not s:
            print(f"  SKIP (no summary): {rname}")
            continue
        base = f"{date} {p}"
        key = base
        n = seen.get(key, 0)
        seen[key] = n + 1
        fname = base + (f" ({n+1})" if n else "") + ".md"
        path = os.path.join(MEET_DIR, fname)
        with open(path, "w") as f:
            f.write(render_meeting(p, date, rname, s, opts))
        written += 1
        if mode == "preview":
            print(f"\n----- PREVIEW: Meetings/{fname} -----\n")
            print(open(path).read())
    print(f"\nWrote {written} meeting notes -> Team Manager/Meetings/  (mode={mode})")


if __name__ == "__main__":
    main()
