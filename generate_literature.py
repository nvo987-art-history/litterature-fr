import json
import ssl
import time
import subprocess
import urllib.parse
import urllib.request
import urllib.error

OUTPUT_FILE = "literature.json"
SPARQL_URL = "https://query.wikidata.org/sparql"
USER_AGENT = "FrenchLiteratureBot/1.0 (https://github.com/nvo987-art-history/litterature-fr)"

ssl_context = ssl.create_default_context()


def log(msg):
    print(msg, flush=True)


def execute_sparql(query, retries=5):
    """SPARQL lekérdezés végrehajtása."""
    data = urllib.parse.urlencode({
        "query": query,
        "format": "json"
    }).encode("utf-8")

    req = urllib.request.Request(
        SPARQL_URL,
        data=data,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded"
        },
        method="POST"
    )

    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60, context=ssl_context) as response:
                content = response.read().decode("utf-8", errors="replace")
                return json.loads(content, strict=False)

        except urllib.error.HTTPError as e:
            if e.code in (429, 502, 503, 504):
                retry_after = e.headers.get("Retry-After")
                wait = int(retry_after) if retry_after and retry_after.isdigit() else min(10 * attempt, 60)
                log(f"  [Újrapróbálkozás {attempt}/{retries}] HTTP {e.code}, várakozás {wait} mp...")
                time.sleep(wait)
            else:
                log(f"  HTTP hiba: {e.code} - {e.reason}")
                return None
        except Exception as e:
            wait = min(10 * attempt, 60)
            log(f"  [Újrapróbálkozás {attempt}/{retries}] Hiba: {e}, várakozás {wait} mp...")
            time.sleep(wait)

    return None


def fetch_all_literature_qids():
    """1. LÉPÉS: Az összes francia irodalmi személy QID lekérése közvetlen Wikidata QID-k alapján."""
    log("Francia irodalmi személyek QID azonosítóinak lekérése...")

    query = """
    SELECT DISTINCT ?person WHERE {
      ?person wdt:P27 wd:Q142 ;
              wdt:P31 wd:Q5 ;
              wdt:P106 ?occupation .

      VALUES ?occupation {
        wd:Q482980
        wd:Q36180
        wd:Q49757
        wd:Q6625963
        wd:Q214917
        wd:Q11774202
        wd:Q15949613
        wd:Q333634
      }
    }
    """

    res = execute_sparql(query)

    if not res:
        return []

    bindings = res.get("results", {}).get("bindings", [])

    qids = [
        item.get("person", {}).get("value", "").rsplit("/", 1)[-1]
        for item in bindings
    ]

    qids = [q for q in qids if q.startswith("Q")]

    log(f"  -> Összesen {len(qids)} irodalmi személy QID azonosítója megtalálva.")

    return qids


def fetch_details_for_batch(qid_chunk):
    """2. LÉPÉS: 2500 elemes kötegek lekérése közvetlen Wikidata foglalkozás-QID-k alapján."""

    values_str = " ".join([f"wd:{qid}" for qid in qid_chunk])

    query = f"""
    SELECT ?person ?personLabel ?occupation ?article ?website WHERE {{
      VALUES ?person {{ {values_str} }}

      OPTIONAL {{
        ?person rdfs:label ?personLabel .
        FILTER(LANG(?personLabel) IN ("fr", "en"))
      }}

      OPTIONAL {{
        ?person wdt:P106 ?occupation .

        VALUES ?occupation {{
          wd:Q482980
          wd:Q36180
          wd:Q49757
          wd:Q6625963
          wd:Q214917
          wd:Q11774202
          wd:Q15949613
          wd:Q333634
        }}
      }}

      OPTIONAL {{
        ?article schema:about ?person ;
                 schema:isPartOf <https://fr.wikipedia.org/> .
      }}

      OPTIONAL {{
        ?person wdt:P856 ?website .
      }}
    }}
    """

    return execute_sparql(query)


def occupation_to_category(occupation):
    """Wikidata foglalkozás-QID → francia irodalmi kategória."""

    occupation = occupation.strip()

    mapping = {
        "Q482980": "Auteurs",
        "Q36180": "Auteurs",
        "Q49757": "Poètes",
        "Q6625963": "Romanciers",
        "Q214917": "Dramaturges",
        "Q11774202": "Essayistes",
        "Q15949613": "Nouvellistes",
        "Q333634": "Traducteurs"
    }

    return mapping.get(occupation)


def save_json(literature_map):
    literature = sorted(
        list(literature_map.values()),
        key=lambda p: p["name"].lower()
    )

    output = {
        "source": "Wikidata (CC0)",
        "count": len(literature),
        "literature": literature
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)


def git_commit_and_push(count):
    log(f"  -> Git commit + push indítása ({count} irodalmi személy)...")

    try:
        subprocess.run(
            [
                "git",
                "config",
                "--global",
                "user.name",
                "github-actions[bot]"
            ],
            check=True
        )

        subprocess.run(
            [
                "git",
                "config",
                "--global",
                "user.email",
                "41898282+github-actions[bot]@users.noreply.github.com"
            ],
            check=True
        )

        subprocess.run(
            ["git", "add", OUTPUT_FILE],
            check=True
        )

        status = subprocess.run(
            ["git", "diff", "--cached", "--quiet"]
        )

        if status.returncode == 0:
            log("  -> Nincs új változás, commit nem szükséges.")
            return True

        subprocess.run(
            [
                "git",
                "commit",
                "-m",
                f"weekly: update literature data ({count})"
            ],
            check=True
        )

        subprocess.run(
            ["git", "push"],
            check=True
        )

        log(
            f"  -> PUSH KÉSZ: {count} irodalmi személy adata feltöltve."
        )

        return True

    except subprocess.CalledProcessError as e:
        log(f"  -> Git hiba: {e}")
        return False


def main():
    literature_map = {}
    has_error = False

    try:
        # 1. Az összes QID megszerzése egyben
        qids = fetch_all_literature_qids()

        if not qids:
            has_error = True
            raise RuntimeError(
                "Nem sikerült lekérni az irodalmi személyek QID azonosítóit."
            )

        # 2. 2500-as nagy kötegekben való letöltés
        batch_size = 2500
        total_batches = (
            len(qids) + batch_size - 1
        ) // batch_size

        for i in range(0, len(qids), batch_size):
            chunk = qids[i:i + batch_size]
            current_batch = (i // batch_size) + 1

            log(
                f"Köteg {current_batch}/{total_batches} "
                f"lekérése ({len(chunk)} elem)..."
            )

            res = fetch_details_for_batch(chunk)

            if not res:
                log(
                    f"  [HIBA] A {current_batch}. köteg "
                    f"letöltése meghiúsult."
                )

                has_error = True
                continue

            bindings = res.get(
                "results",
                {}
            ).get(
                "bindings",
                []
            )

            for item in bindings:
                person_uri = (
                    item
                    .get("person", {})
                    .get("value", "")
                    .strip()
                )

                qid = person_uri.rsplit("/", 1)[-1]

                name = (
                    item
                    .get("personLabel", {})
                    .get("value", "")
                    .strip()
                )

                occupation = (
                    item
                    .get("occupation", {})
                    .get("value", "")
                    .strip()
                )

                occupation_qid = occupation.rsplit("/", 1)[-1]

                wikipedia = (
                    item
                    .get("article", {})
                    .get("value", "")
                    .strip()
                )

                website = (
                    item
                    .get("website", {})
                    .get("value", "")
                    .strip()
                )

                category = occupation_to_category(
                    occupation_qid
                )

                if not name or name == qid:
                    continue

                if qid not in literature_map:
                    literature_map[qid] = {
                        "name": name,
                        "categories": [
                            "Auteurs"
                        ],
                        "wikidata": (
                            f"https://www.wikidata.org/wiki/{qid}"
                        ),
                        "wikipedia": wikipedia,
                        "website": website
                    }

                if (
                    category
                    and category not in literature_map[qid]["categories"]
                ):
                    literature_map[qid]["categories"].append(
                        category
                    )

                if (
                    wikipedia
                    and not literature_map[qid]["wikipedia"]
                ):
                    literature_map[qid]["wikipedia"] = wikipedia

                if (
                    website
                    and not literature_map[qid]["website"]
                ):
                    literature_map[qid]["website"] = website

            log(
                f"  -> Jelenleg feldolgozva: "
                f"{len(literature_map)} irodalmi személy"
            )

            time.sleep(1)

    except Exception as e:
        log(
            f"Váratlan hiba történt a futás során: {e}"
        )

        has_error = True

    finally:
        # Bármi történik, az eddigi adatokat elmentjük és PUSH-oljuk
        if literature_map:
            log(
                f"-> {len(literature_map)} irodalmi személy "
                f"adatainak elmentése és PUSH-olása a GitHubra..."
            )

            save_json(literature_map)

            git_commit_and_push(
                len(literature_map)
            )

        else:
            log(
                "-> Nincs menthető adat."
            )

        if has_error:
            raise RuntimeError(
                "A folyamat nem fejeződött be 100%-osan, "
                "de a részleges adatok mentve lettek."
            )


if __name__ == "__main__":
    main()
