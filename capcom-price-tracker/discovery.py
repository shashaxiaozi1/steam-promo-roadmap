"""Find new publisher AppIDs in the US Steam storefront and propose standard packages."""

from __future__ import annotations

import datetime as dt
from html import unescape
from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import urlencode, urlsplit

from publisher_config import PUBLISHER_CONFIG


STORE = "https://store.steampowered.com"
PAGE_SIZE = 50
MAX_RESULTS_PER_ALIAS = 500


class DiscoveryError(Exception):
    pass


class SearchPage(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows = 0
        self.appids: set[int] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        if tag != "a" or "search_result_row" not in (attr.get("class") or "").split():
            return
        self.rows += 1
        link = urlsplit(attr.get("href") or "")
        match = re.match(r"^/app/([1-9]\d*)(?:/|$)", link.path)
        if match and link.netloc in ("", "store.steampowered.com"):
            self.appids.add(int(match[1]))


def name_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", value.replace("™", "").replace("®", "")).casefold()
    return "".join(c for c in text if c.isalnum())


def search_publisher(publisher: str, request) -> set[int]:
    appids: set[int] = set()
    for start in range(0, MAX_RESULTS_PER_ALIAS, PAGE_SIZE):
        query = urlencode({
            "publisher": publisher, "category1": 998, "infinite": 1,
            "start": start, "count": PAGE_SIZE, "cc": "us", "l": "english",
        })
        response = request(f"{STORE}/search/results/?{query}")
        body = response.get("body")
        if not isinstance(body, dict):
            raise DiscoveryError(f"search response unavailable (HTTP {response.get('http_status', 'unknown')})")
        try:
            total = int(body["total_count"])
            html = body["results_html"]
        except (KeyError, TypeError, ValueError) as exc:
            raise DiscoveryError("unexpected search response") from exc
        if not isinstance(html, str) or not 0 <= total <= MAX_RESULTS_PER_ALIAS:
            raise DiscoveryError("unexpected search HTML or result count")
        page = SearchPage()
        page.feed(html)
        if start < total and not page.rows:
            raise DiscoveryError("search page reported results but contained no rows")
        appids.update(page.appids)
        if start + PAGE_SIZE >= total:
            return appids
    raise DiscoveryError("search pagination did not finish")


def release_date(value: str) -> dt.date | None:
    for fmt in ("%b %d, %Y", "%d %b, %Y", "%B %d, %Y", "%d %B, %Y"):
        try:
            return dt.datetime.strptime(value, fmt).date()
        except ValueError:
            pass
    return None


def paid_options(app: dict) -> dict[int, tuple[str, int]]:
    options = {}
    for group in app.get("package_groups") or []:
        if not isinstance(group, dict):
            continue
        if str(group.get("is_recurring_subscription", "false")).lower() not in ("0", "false"):
            continue
        for sub in group.get("subs") or []:
            if not isinstance(sub, dict) or sub.get("is_free_license") is not False:
                continue
            label = unescape(re.sub(r"<[^>]+>", "", str(sub.get("option_text", ""))))
            label = re.split(r"\s+-\s+\$", label, maxsplit=1)[0].strip()
            label = re.sub(r"^(?:pre[- ]?purchase|pre[- ]?order)\s+", "", label, flags=re.I)
            pid, cents = sub.get("packageid"), sub.get("price_in_cents_with_discount")
            if label and type(pid) is int and pid > 0 and type(cents) is int and cents >= 0:
                if pid in options and options[pid] != (label, cents):
                    raise DiscoveryError("conflicting_store_options_for_package")
                options[pid] = (label, cents)
    return options


def standard_options(app: dict, title: str) -> dict[int, int]:
    target = name_key(title)
    accepted = {target, target + "standard", target + "standardedition"}
    if (app.get("release_date") or {}).get("coming_soon") is True:
        accepted |= {name + suffix for name in accepted
                     for suffix in ("preorder", "prepurchase", "withprepurchasebonus")}
    return {pid: cents for pid, (label, cents) in paid_options(app).items()
            if name_key(label) in accepted}


def edition_options(app: dict, title: str) -> list[dict]:
    """Recognize full editions by their store label; unknown offers stay in review."""
    target = name_key(title)
    editions = []
    for pid, (label, cents) in paid_options(app).items():
        key = name_key(label)
        tail = key.removeprefix(target).removesuffix("withprepurchasebonus")
        if re.search(r"upgrade|seasonpass|soundtrack|artbook|trial|demo|additional|(?:weapon|costume|scenario)set|add(?:on|ons)|dlc", tail):
            continue
        if not key.startswith(target) or not (
            re.fullmatch(r"(?:digital)?(?:deluxe|premium|ultimate|gold|complete|definitive|collector(?:s)?)(?:deluxe)?(?:edition)?", tail)
            or (tail.endswith("edition") and tail not in ("standardedition", "baseedition"))
        ):
            return []
        editions.append({"package_id": pid, "label": label, "store_option_price_cents": cents})
    return editions


def evaluate(appid: int, app: dict, decision: dict, publisher: str = "CAPCOM") -> dict | None:
    aliases, release_from = PUBLISHER_CONFIG[publisher]
    title = app.get("name")
    if type(app.get("steam_appid")) is not int or app["steam_appid"] != appid or not isinstance(title, str) or not title.strip():
        raise DiscoveryError("app_identity_not_verified")
    decision["game_name"] = title
    if app.get("type") != "game" or app.get("is_free") is True:
        decision.update(status="excluded", reason="not_a_paid_base_game")
        return None
    if app.get("is_free") is not False:
        raise DiscoveryError("paid_status_not_verified")
    publishers = app.get("publishers") or []
    decision["steam_publishers"] = publishers
    if not isinstance(publishers, list) or not (
        {name_key(x) for x in publishers if isinstance(x, str)}
        & {name_key(x) for x in aliases}
    ):
        raise DiscoveryError("publisher_not_verified")
    if re.search(r"\b(?:Z\s*Version|Deluxe Edition|Gold Edition|Ultimate Edition|Premium Edition|Complete Edition)\s*$|グロテスク", title, re.I):
        raise DiscoveryError("possible_regional_or_nonstandard_edition")
    release = app.get("release_date") or {}
    if not isinstance(release, dict) or type(release.get("coming_soon")) is not bool:
        raise DiscoveryError("release_status_not_verified")
    date_text = str(release.get("date") or "")
    decision.update(coming_soon=release["coming_soon"], steam_store_release_date=date_text)
    if not release["coming_soon"]:
        date = release_date(date_text)
        if date is None or date < release_from:
            raise DiscoveryError("older_or_undated_title_requires_review")
    options = standard_options(app, title)
    decision["standard_package_ids"] = sorted(options)
    if not options and release["coming_soon"] and not app.get("package_groups") and not app.get("price_overview"):
        decision.update(status="pending", reason="awaiting_US_preorder_or_release")
        return None
    if not options:
        editions = edition_options(app, title)
        if not editions:
            raise DiscoveryError("standard_US_purchase_package_not_unique")
        decision["edition_options"] = editions
        decision["linked_dlc_appids"] = [x for x in (app.get("dlc") or ()) if type(x) is int]
        pid = None  # selected after all edition packages have been verified
    elif len(options) == 1:
        pid, cents = next(iter(options.items()))
        decision.update(package_id=pid, store_option_price_cents=cents)
    else:
        raise DiscoveryError("standard_US_purchase_package_not_unique")
    return {
        "publisher": publisher, "appid": appid, "game_name": title,
        "package_id": pid, "tracked_edition": "Base",
        "steam_store_release_date": date_text,
        "release_date_source": f"{STORE}/app/{appid}/?cc=us&l=english",
    }


def scan(known: set[int], request, *, tracked_names: tuple[str, ...] = (),
         publisher: str = "CAPCOM") -> tuple[list, dict]:
    found: set[int] = set()
    errors: list[dict] = []
    sources = {}
    for alias in PUBLISHER_CONFIG[publisher][0]:
        try:
            ids = search_publisher(alias, request)
            sources[alias] = len(ids)
            found.update(ids)
        except (DiscoveryError, ValueError) as exc:
            errors.append({"publisher": alias, "reason": str(exc)})
    candidates = []
    proposed = []
    names = {name_key(x) for x in tracked_names}
    for appid in sorted(found - known):
        decision = {"appid": appid, "status": "review"}
        candidates.append(decision)
        url = f"{STORE}/api/appdetails?" + urlencode({"appids": appid, "cc": "us", "l": "english"})
        response = request(url)
        body = response.get("body")
        entry = body.get(str(appid)) if isinstance(body, dict) else None
        if not isinstance(entry, dict) or entry.get("success") is not True or not isinstance(entry.get("data"), dict):
            decision["reason"] = "US_app_details_unavailable"
            continue
        try:
            item = evaluate(appid, entry["data"], decision, publisher)
        except DiscoveryError as exc:
            decision["reason"] = str(exc)
            continue
        if item:
            proposed.append((item, decision))
    proposed_names = [name_key(item["game_name"]) for item, _ in proposed]
    approved = []
    for (item, decision), key in zip(proposed, proposed_names):
        if key in names or proposed_names.count(key) > 1:
            decision["reason"] = "same_title_different_appid"
        else:
            approved.append((item, decision))
    return approved, {"sources": sources, "appids_seen": len(found), "errors": errors, "candidates": candidates}
