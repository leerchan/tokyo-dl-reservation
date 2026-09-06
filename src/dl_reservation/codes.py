"""Constants for the tokyo-madoguchi-yoyaku booking site.

Source: MKAYMA001data.js, MKAYMA001placeData.json (verified 2026-05-08).
See OBSERVATIONS.md and DESIGN.md §"Discovered facts" for the upstream
references.
"""

from __future__ import annotations

API_BASE = "https://license-test-tokyo-prd-police-pref-api.tokyo-madoguchi-yoyaku.com"
SITE_BASE = "https://license-test.tokyo-madoguchi-yoyaku.com/police-pref-tokyo/01"
USER_TOKEN = "pub"  # window.userInfo literal — required by /calgetres
# WAF (2026-09) 403s non-browser UAs; Origin/Referer alone don't pass.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

# placecode → 試験場 name (Tokyo)
PLACES: dict[str, str] = {
    "270": "府中試験場",
    "280": "鮫洲試験場",
    "250": "江東試験場",
}

# typeDetailNum → human label
COURSES: dict[str, str] = {
    "1": "YURYOU",
    "2": "IPPAN",
    "3": "IHAN",
    "4": "SHOKAI",
    "7": "KOUREI",
    "11": "KYOSOTSU",
    "12": "IPPAN-SHIKEN",
    "13": "GENTSUKI",
    "14": "SHOTOKU",
    "15": "NISHU",
    "21": "NINCHI",
    "31": "GAIMEN",
    "61": "KYOSOTSU-MAINA",
}
