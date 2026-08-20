# patch_id:        fix-provider-prefix-shadowing
# upstream_file:   backend/app/schemas/enums/provider.py
# upstream_symbol: ProviderName.from_source_string
# retire_when:     ProviderName.from_source_string resolves "garmin_connect" to ProviderName.GARMIN_CONNECT rather than ProviderName.GARMIN. Marker: any longest-match / sorted-by-length logic (or an explicit alias table) inside from_source_string.

"""Stop `garmin` shadowing `garmin_connect` when inferring a provider.

Bug
---
`ProviderName.from_source_string` substring-matches the source string against
each enum value **in declaration order** and returns the first hit::

    source_lower = source.lower()
    for provider in cls:
        if provider in (cls.UNKNOWN, cls.INTERNAL):
            continue
        if provider.value in source_lower:
            return provider
    return cls.UNKNOWN

`GARMIN = "garmin"` is declared before `GARMIN_CONNECT = "garmin_connect"`, and
`"garmin" in "garmin_connect"` is True — so **every** garmin_connect source
resolves to `ProviderName.GARMIN` and `GARMIN_CONNECT` is unreachable through
this function. It is a general prefix-shadowing flaw: any provider whose value
is a prefix of another provider's value shadows it, whichever is declared first.

Why it matters beyond a wrong label
-----------------------------------
The result is **persisted**, not just displayed. `infer_provider_from_source`
delegates here and is called on the write path in four places:

  * `event_record_repository.py:57` and `:190`
  * `data_point_series_repository.py:131` and `:251`

so every `data_source` row created for garmin_connect stores
`provider = 'garmin'`. Downstream consequences:

  * `GET /summaries/body` (and the other summaries) report
    ``provider: "garmin"`` alongside ``source: "garmin_connect"``.
  * Provider-priority resolution keys off the provider enum
    (`summaries_service.py:119`, and `fix-health-score-source-priority`), so
    garmin_connect is ranked in GARMIN's priority slot instead of its own — the
    wrong source can win a de-duplication against Ultrahuman.

Fix
---
Match the **longest** provider value first. Sorting candidates by descending
value length means `garmin_connect` is tested before `garmin`, so the more
specific provider wins while every other lookup is unchanged. This is a pure
function with no state, so the patch is a straight swap.

Deliberately NOT changed: enum declaration order (that would be a structural
edit to an upstream file, and any future re-ordering would silently reintroduce
the bug), and the UNKNOWN/INTERNAL exclusions.

Note this only prevents new bad rows. Existing rows are corrected by the
migration `fix_garmin_connect_provider_mislabel`.
"""

from __future__ import annotations


def _from_source_string(cls, source: str | None):  # noqa: ANN001, ANN202
    """Infer provider from a source string, preferring the most specific match.

    Args:
        source: Source string (e.g. "apple_health_sdk", "Garmin Connect").

    Returns:
        Matching ProviderName, or UNKNOWN if nothing matches.
    """
    if not source:
        return cls.UNKNOWN

    # Normalise separators so a human-readable source name matches the enum's
    # snake_case value: "Garmin Connect" and "garmin-connect" must both reach
    # GARMIN_CONNECT, not fall through to the shorter GARMIN. EventRecord carries
    # both a machine `source` ("garmin_connect") and a display `source_name`
    # ("Garmin Connect"), and either can reach this function.
    source_lower = source.lower().replace(" ", "_").replace("-", "_")

    # Longest value first so a prefix cannot shadow a more specific provider
    # (e.g. "garmin" must not win over "garmin_connect"). Ties broken by value
    # for a deterministic result.
    candidates = sorted(
        (p for p in cls if p not in (cls.UNKNOWN, cls.INTERNAL)),
        key=lambda p: (-len(p.value), p.value),
    )

    for provider in candidates:
        if provider.value in source_lower:
            return provider

    return cls.UNKNOWN


def install() -> None:
    """Replace ProviderName.from_source_string with the longest-match version."""
    import sys  # noqa: PLC0415

    import app.schemas.enums.provider  # noqa: F401, PLC0415

    module = sys.modules["app.schemas.enums.provider"]
    module.ProviderName.from_source_string = classmethod(_from_source_string)
