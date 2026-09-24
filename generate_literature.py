import json
import ssl
import time
import subprocess
import urllib.parse
import urllib.request
import urllib.error

OUTPUT_FILE = "literature.json"
SPARQL_URL = "https://query.wikidata.org/sparql"
WIKIDATA_API_URL = "https://www.wikidata.org/w/api.php"
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
            with urllib.request.urlopen(
                req,
                timeout=120,
                context=ssl_context
            ) as response:
                content = response.read().decode(
                    "utf-8",
                    errors="replace"
                )
                return json.loads(content, strict=False)

        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                wait = min(15 * attempt, 90)
                log(
                    f"  [Újrapróbálkozás {attempt}/{retries}] "
                    f"HTTP {e.code}, várakozás {wait} mp..."
                )
                time.sleep(wait)
            else:
                log(f"  HTTP hiba: {e.code} - {e.reason}")
                return None

        except Exception as e:
            wait = min(15 * attempt, 90)
            log(
                f"  [Újrapróbálkozás {attempt}/{retries}] "
                f"Hiba: {e}, várakozás {wait} mp..."
            )
            time.sleep(wait)

    return None


def fetch_all_literature_qids():
    """Francia irodalmi személyek QID-jainak lekérése kategóriánként."""

    occupations = {
        "Q482980": "Auteurs",
        "Q49757": "Poètes",
        "Q6625963": "Romanciers",
        "Q214917": "Dramaturges",
        "Q11774202": "Essayistes",
        "Q15949613": "Nouvellistes",
        "Q333634": "Traducteurs"
    }

    people = {}

    log("Francia irodalmi személyek QID azonosítóinak lekérése...")

    for occupation_qid, category in occupations.items():

        log(
            f"  -> {category} ({occupation_qid}) lekérése..."
        )

        query = f"""
        SELECT DISTINCT ?person WHERE {{
          ?person wdt:P106 wd:{occupation_qid} ;
                  wdt:P27 wd:Q142 ;
                  wdt:P31 wd:Q5 .
        }}
        """

        res = execute_sparql(query)

        if not res:
            log(
                f"  [HIBA] A(z) {category} kategória lekérése "
                f"meghiúsult."
            )
            continue

        bindings = res.get(
            "results",
            {}
        ).get(
            "bindings",
            []
        )

        found = 0

        for item in bindings:
            value = (
                item
                .get("person", {})
                .get("value", "")
                .strip()
            )

            qid = value.rsplit("/", 1)[-1]

            if not qid.startswith("Q"):
                continue

            if qid not in people:
                people[qid] = set()

            people[qid].add(category)
            found += 1

        log(
            f"     -> {found} személy található ebben a kategóriában."
        )

        time.sleep(2)

    qids = list(people.keys())

    log(
        f"  -> Összesen {len(qids)} egyedi irodalmi személy "
        f"QID azonosítója megtalálva."
    )

    return qids, people


def fetch_wikidata_entities(qid_chunk):
    """Wikidata adatok lekérése API-n keresztül."""

    params = {
        "action": "wbgetentities",
        "ids": "|".join(qid_chunk),
        "props": "labels|claims|sitelinks",
        "languages": "fr|en",
        "sitefilter": "frwiki",
        "format": "json"
    }

    url = (
        WIKIDATA_API_URL
        + "?"
        + urllib.parse.urlencode(params)
    )

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json"
        }
    )

    for attempt in range(1, 6):
        try:
            with urllib.request.urlopen(
                req,
                timeout=60,
                context=ssl_context
            ) as response:
                content = response.read().decode(
                    "utf-8",
                    errors="replace"
                )

                return json.loads(
                    content,
                    strict=False
                )

        except Exception as e:
            wait = min(10 * attempt, 60)

            log(
                f"  [API újrapróbálkozás {attempt}/5] "
                f"Hiba: {e}, várakozás {wait} mp..."
            )

            time.sleep(wait)

    return None


def get_label(entity):
    """Francia, majd angol címke."""

    labels = entity.get("labels", {})

    if "fr" in labels:
        return labels["fr"].get("value", "").strip()

    if "en" in labels:
        return labels["en"].get("value", "").strip()

    return ""


def get_website(entity):
    """P856 website lekérése."""

    claims = entity.get("claims", {})

    for claim in claims.get("P856", []):
        mainsnak = claim.get("mainsnak", {})
        datavalue = mainsnak.get("datavalue", {})

        value = datavalue.get("value")

        if isinstance(value, str) and value.strip():
            return value.strip()

    return ""


def get_french_wikipedia(entity):
    """Francia Wikipedia oldal lekérése."""

    sitelinks = entity.get("sitelinks", {})

    frwiki = sitelinks.get("frwiki", {})

    title = frwiki.get("title", "").strip()

    if not title:
        return ""

    encoded_title = urllib.parse.quote(
        title.replace(" ", "_"),
        safe=""
    )

    return (
        "https://fr.wikipedia.org/wiki/"
        + encoded_title
    )


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

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            output,
            f,
            ensure_ascii=False,
            indent=2
        )


def git_commit_and_push(count):
    log(
        f"  -> Git commit + push indítása "
        f"({count} irodalmi személy)..."
    )

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
            log(
                "  -> Nincs új változás, commit nem szükséges."
            )
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
            f"  -> PUSH KÉSZ: {count} irodalmi személy "
            f"adata feltöltve."
        )

        return True

    except subprocess.CalledProcessError as e:
        log(f"  -> Git hiba: {e}")
        return False


def main():
    literature_map = {}
    has_error = False

    try:
        # 1. QID-ok kategóriánként, külön SPARQL lekérdezésekkel
        qids, categories = fetch_all_literature_qids()

        if not qids:
            has_error = True
            raise RuntimeError(
                "Nem sikerült lekérni az irodalmi személyek "
                "QID azonosítóit."
            )

        # 2. Wikidata API 50-es kötegekben
        batch_size = 50

        total_batches = (
            len(qids) + batch_size - 1
        ) // batch_size

        for i in range(0, len(qids), batch_size):

            chunk = qids[i:i + batch_size]

            current_batch = (
                i // batch_size
            ) + 1

            log(
                f"Köteg {current_batch}/{total_batches} "
                f"lekérése ({len(chunk)} személy)..."
            )

            data = fetch_wikidata_entities(chunk)

            if not data:
                log(
                    f"  [HIBA] A {current_batch}. köteg "
                    f"letöltése meghiúsult."
                )

                has_error = True
                continue

            entities = data.get(
                "entities",
                {}
            )

            for qid in chunk:

                entity = entities.get(qid)

                if not entity:
                    continue

                name = get_label(entity)

                if not name or name == qid:
                    continue

                wikipedia = get_french_wikipedia(
                    entity
                )

                website = get_website(
                    entity
                )

                person_categories = sorted(
                    list(
                        categories.get(
                            qid,
                            set()
                        )
                    )
                )

                if "Auteurs" not in person_categories:
                    person_categories.insert(
                        0,
                        "Auteurs"
                    )

                literature_map[qid] = {
                    "name": name,
                    "categories": person_categories,
                    "wikidata": (
                        f"https://www.wikidata.org/wiki/{qid}"
                    ),
                    "wikipedia": wikipedia,
                    "website": website
                }

            log(
                f"  -> Jelenleg feldolgozva: "
                f"{len(literature_map)} irodalmi személy"
            )

            time.sleep(0.5)

    except Exception as e:

        log(
            f"Váratlan hiba történt a futás során: {e}"
        )

        has_error = True

    finally:

        if literature_map:

            log(
                f"-> {len(literature_map)} irodalmi személy "
                f"adatainak elmentése és PUSH-olása a GitHubra..."
            )

            save_json(
                literature_map
            )

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
