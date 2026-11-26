"""Offline tests for the clone Group worker's AniList client and cards."""
import asyncio
import json

import httpx
import pytest

from group_worker.anime import (
    AniListClient,
    AniListError,
    _browse_filter,
    _character_card,
    _media_card,
    _safe_url,
)


def test_anilist_search_uses_fixed_endpoint_variables_and_short_cache():
    requests = []
    item = {"id": 7, "title": {"english": "Sample Show"}, "type": "ANIME"}

    async def mock_request(request):
        requests.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"data": {"Media": item}})

    async def scenario():
        client = AniListClient(transport=httpx.MockTransport(mock_request))
        first, second = await asyncio.gather(
            client.media("  Sample    Show ", "ANIME"),
            client.media("Sample Show", "ANIME"),
        )
        assert first == item and second == item
        assert len(requests) == 1
        url, payload = requests[0]
        assert url == "https://graphql.anilist.co"
        assert payload["variables"] == {"search": "Sample Show", "type": "ANIME"}
        assert "isAdult: false" in payload["query"]

    from tests.harness import run
    run(scenario())


def test_anilist_rejects_graphql_errors_without_leaking_provider_detail():
    async def mock_request(_request):
        return httpx.Response(200, json={"errors": [{"message": "private upstream detail"}]})

    async def scenario():
        client = AniListClient(transport=httpx.MockTransport(mock_request))
        with pytest.raises(AniListError, match="invalid response"):
            await client.media("Title", "MANGA")

    from tests.harness import run
    run(scenario())


def test_anilist_browse_filters_and_url_allowlist():
    assert _browse_filter("trending") == ("TRENDING_DESC", None)
    assert _browse_filter("upcoming") == ("START_DATE", "NOT_YET_RELEASED")
    assert _browse_filter("latest") == ("UPDATED_AT_DESC", "RELEASING")
    assert _browse_filter("top") == ("SCORE_DESC", None)
    with pytest.raises(ValueError):
        _browse_filter("unknown")
    assert _safe_url("https://anilist.co/anime/123") == "https://anilist.co/anime/123"
    assert _safe_url("javascript:alert(1)") is None
    assert _safe_url("https://evil.example/anime/123") is None


def test_anilist_cards_escape_untrusted_text_and_hide_spoiler_tags():
    media = {
        "title": {"english": "<img src=x onerror=alert(1)>"},
        "format": "TV", "status": "RELEASING", "averageScore": 91,
        "startDate": {"year": 2026, "month": 4, "day": 3},
        "genres": ["Action"],
        "tags": [
            {"name": "Adventure", "rank": 80, "isMediaSpoiler": False, "isGeneralSpoiler": False},
            {"name": "Plot secret", "rank": 99, "isMediaSpoiler": True, "isGeneralSpoiler": False},
        ],
        "description": "<img onerror=alert(1)>Story <b>text</b>",
        "nextAiringEpisode": {"episode": 4, "airingAt": 1770000000},
    }
    card = _media_card(media, "anime", airing=True)
    assert "&lt;img" in card and "onerror=alert(1)&gt;" in card
    assert "<img" not in card and "<script" not in card
    assert "Adventure" in card and "Plot secret" not in card
    assert "Next: episode 4" in card

    character = _character_card({
        "name": {"full": "A <b>Name</b>"},
        "description": "A <script>bad()</script> character",
        "favourites": 5,
    })
    assert "A &lt;b&gt;Name&lt;/b&gt;" in character
    assert "<script>" not in character and "Favourites: 5" in character
