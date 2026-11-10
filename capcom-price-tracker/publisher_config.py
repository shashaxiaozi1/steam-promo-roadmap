"""Publisher registry shared by Steam discovery, collection, and GitHub Actions."""

from datetime import date
import json


# Keys are stable Power BI names. Values are the file stem, earliest automatic
# release date, and Steam storefront publisher names to search and verify.
PUBLISHERS = {
    "BANDAI NAMCO": ("bandai", "2026-11-09", (
        "Bandai Namco Entertainment", "Bandai Namco Entertainment Inc.",
        "BANDAI NAMCO Entertainment", "BANDAI NAMCO Entertainment Inc.",
        "Bandai Namco Entertainment ", "BANDAI NAMCO Entertainment ",
        "Bandai Namco Games Inc.", "Namco Bandai Games Inc.",
    )),
    "CAPCOM": ("capcom", "2026-11-06", ("Capcom", "CAPCOM Co., Ltd.", "Capcom U.S.A., Inc.")),
    "KONAMI": ("konami", "2026-11-10", ("KONAMI",)),
    "KOEI TECMO": ("koei_tecmo", "2026-11-10", ("KOEI TECMO GAMES CO., LTD.",)),
    "SEGA": ("sega", "2026-11-10", ("SEGA",)),
    "SQUARE ENIX": ("square_enix", "2026-11-10", ("Square Enix",)),
    "PARADOX INTERACTIVE": ("paradox_interactive", "2026-11-10", ("Paradox Interactive",)),
    "DEVOLVER DIGITAL": ("devolver_digital", "2026-11-10", ("Devolver Digital",)),
    "VALVE": ("valve", "2026-11-10", ("Valve",)),
}

PUBLISHER_CONFIG = {
    publisher: (store_names, date.fromisoformat(release_from))
    for publisher, (_, release_from, store_names) in PUBLISHERS.items()
}

# Keep the filenames used by earlier runs and linked reports.
PUBLISHER_FILES = {
    publisher: (f"catalog_{stem}.csv", f"excluded_{stem}.csv", f"{stem}.json")
    for publisher, (stem, _, _) in PUBLISHERS.items()
}
PUBLISHER_FILES["CAPCOM"] = ("catalog.csv", "excluded.csv", "latest.json")
PUBLISHER_FILES["BANDAI NAMCO"] = ("catalog_bandai.csv", "excluded_bandai.csv", "bandai_namco.json")


if __name__ == "__main__":
    print(json.dumps([{"publisher": name, "slug": stem.replace("_", "-")}
                      for name, (stem, _, _) in PUBLISHERS.items()], separators=(",", ":")))
