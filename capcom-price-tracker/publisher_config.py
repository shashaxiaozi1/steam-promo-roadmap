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
    "VALVE": ("valve", "2026-11-10", ("Valve",)),
    "FOCUS ENTERTAINMENT": ("focus_entertainment", "2026-11-10", (
        "Focus Entertainment", "Focus Home Interactive", "Focus Entertainment Publishing",
    )),
    "2K": ("2k", "2026-11-10", ("2K", "2K Games", "2K Games, Inc.")),
    "ROCKSTAR GAMES": ("rockstar_games", "2026-11-10", ("Rockstar Games", "Rockstar Games, Inc.")),
    "WARNER BROS. GAMES": ("warner_bros_games", "2026-11-10", (
        "Warner Bros. Games", "Warner Bros. Interactive Entertainment", "WB Games", "Warner Bros. Interactive",
    )),
    "ELECTRONIC ARTS": ("electronic_arts", "2026-11-10", ("Electronic Arts", "EA", "Electronic Arts Inc.")),
    "UBISOFT": ("ubisoft", "2026-11-10", ("Ubisoft", "Ubisoft Entertainment", "Ubisoft Entertainment SA")),
    "ACTIVISION": ("activision", "2026-11-10", (
        "Activision", "Activision Publishing", "Activision Publishing, Inc.",
        "Activision (Excluding Japan and Asia)",
    )),
    "KRAFTON, INC.": ("krafton", "2026-11-10", ("KRAFTON, Inc.", "KRAFTON", "KRAFTON Inc.")),
    "BETHESDA SOFTWORKS": ("bethesda_softworks", "2026-11-10", ("Bethesda Softworks", "Bethesda Softworks LLC")),
    "XBOX GAME STUDIOS": ("xbox_game_studios", "2026-11-10", (
        "Xbox Game Studios", "Microsoft Studios", "Microsoft Game Studios",
    )),
    "PLAYSTATION PUBLISHING LLC": ("playstation_publishing", "2026-11-10", (
        "PlayStation Publishing LLC", "PlayStation PC LLC",
    )),
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
