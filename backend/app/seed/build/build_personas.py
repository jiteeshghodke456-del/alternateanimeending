"""Stratify raw Nemotron-Personas-India rows (en_IN) into 240 personas across 6 segments.

Input: a JSON list of rows fetched from the HF datasets-server (see fetch step in README of this folder).
Usage: python -I build_personas.py RAW_JSON OUT_JSON
Segment heuristics (the dataset has no city tier or student flag, so these are proxies):
  small_business : occupation is a proprietor / retail or wholesale dealer, age >= 21
  students       : age 17-24, education Higher Secondary or Graduate+, no occupation or persona mentions study
  18-24 urban    : age 18-24, zone Urban, district in a metro list
  25-40 urban    : age 25-40, zone Urban, metro district
  25-40 tier2/3  : age 25-40, rural, or urban in a non-metro district
  41-60          : age 41-60, any
"""
import json, random, re, sys

METRO = {"Bangalore", "Mumbai", "Mumbai Suburban", "Thane", "Kolkata", "Chennai", "Hyderabad", "Rangareddy",
         "Ahmadabad", "Pune", "North West", "North East", "North", "South", "South West", "West", "East", "Central",
         "New Delhi", "Gurgaon"}
BIZ = re.compile(r"proprietor|retail dealer|wholesale dealer", re.I)
STUDY = re.compile(r"student|studying|college|university|pursuing|exam|b\.?tech|degree|diploma|coaching|undergrad", re.I)
EDU_OK = ("Higher Secondary", "Graduate")
SOURCE = "nvidia/Nemotron-Personas-India (CC-BY-4.0)"


def short(text: str, n: int = 300) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    m = max(cut.rfind(". "), cut.rfind("; "))
    if m > 120:
        return cut[: m + 1]
    return cut[: cut.rfind(" ")].rstrip(",;:") + "..."


def segment(r):
    age, zone, dist, occ, edu = r["age"], r["zone"], r["district"], r["occupation"], r["education_level"]
    metro = zone == "Urban" and dist in METRO
    if age >= 21 and BIZ.search(occ):
        return "small_business"
    if 17 <= age <= 24 and edu.startswith(EDU_OK) and (occ.startswith("No Occupation") or STUDY.search(r["persona"])) \
            and STUDY.search(r["persona"] + " " + edu):
        return "students"
    if 18 <= age <= 24 and metro:
        return "18-24 urban"
    if 25 <= age <= 40 and metro:
        return "25-40 urban"
    if 25 <= age <= 40:
        return "25-40 tier2/3"
    if 41 <= age <= 60:
        return "41-60"
    return None


def main(raw, out, per=40):
    rows = json.load(open(raw))
    rnd = random.Random(2026)
    rnd.shuffle(rows)
    buckets: dict[str, list] = {}
    for r in rows:
        s = segment(r)
        if s:
            buckets.setdefault(s, []).append(r)
    res = []
    for s in ["18-24 urban", "25-40 urban", "25-40 tier2/3", "41-60", "students", "small_business"]:
        got = buckets.get(s, [])
        print(s, "available", len(got))
        for r in got[:per]:
            res.append({"id": r["uuid"], "segment": s, "age": r["age"], "sex": r["sex"], "state": r["state"],
                        "district": r["district"], "urban_rural": r["zone"], "occupation": r["occupation"],
                        "persona": short(r["persona"]), "_source": SOURCE})
    json.dump(res, open(out, "w"), ensure_ascii=False, indent=1)
    print("written", len(res))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
