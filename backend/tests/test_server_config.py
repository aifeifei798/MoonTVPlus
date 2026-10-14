"""`src/app/api/server-config/route.ts` — the pre-login config read.

The login page calls this before anyone is authenticated, so the auth matcher
leaves it open: an unauthenticated GET answers 200 with the two fields the page
needs. `SiteName` is never absent — `refine_config` fills it with
`env(SITE_NAME, "MoonTV")` when the stored blob has none, so the key is always
present on the wire (JS's `JSON.stringify` would have dropped an `undefined`,
which is why the fill matters).

The panel's 站点设置 form (`POST /api/admin/site`) is validated field by field:
nine fields are **required** (`SITE_REQUIRED_TYPES`), so a partial body is a 400
`参数格式错误` — the harness has to send the whole form, exactly as the panel does.
The form rebuilds `SiteConfig` from the body, and `strip_none` at serialization
drops the optional fields it did not send, so the stored blob carries exactly the
nine it sent (`None` == JS `undefined`). `refine_config` then fills the two
optional fields it is asked to serve (`TVBoxEnabled` False, `TVBoxPassword` ""),
and it writes `SiteInterfaceCacheTime` into the blob's `ConfigFile.cache_time` as
well. That second write is what makes the cache time reach the cached routes:
`refine_config` re-injects `cache_time` from the parsed `ConfigFile` on every read
(the same re-injection family as `api_site` and `custom_category`, PLAN.md change
17), so the number the panel sent is the `max-age` the detail route emits, and a
config file that carries `cache_time` outranks the panel's field on the next read.
"""

from __future__ import annotations

import json

from moontv.storage.db import Db


def _site_form(**overrides) -> dict:
    """The whole 站点设置 form: the nine required fields the validator demands."""
    form = {
        "SiteName": "MoonTV",
        "Announcement": "hello",
        "SearchDownstreamMaxPage": 2,
        "SiteInterfaceCacheTime": 7200,
        "DoubanProxyType": "direct",
        "DoubanProxy": "",
        "DoubanImageProxyType": "direct",
        "DoubanImageProxy": "",
        "DisableYellowFilter": False,
    }
    form.update(overrides)
    return form


def test_server_config_needs_no_cookie(client, clean_db):
    client.cookies.pop("auth", None)
    response = client.get("/api/server-config")
    assert response.status_code == 200
    body = response.json()
    assert list(body) == ["SiteName", "StorageType"]
    # backend/config.json's SiteConfig is null, so the env default fills it.
    assert body["SiteName"] == "MoonTV"
    assert body["StorageType"] == "sqlite"


def test_server_config_rejects_a_partial_form(client, cookie, clean_db):
    # The nine required fields are checked before anything is written.
    response = client.post(
        "/api/admin/site",
        headers={"Cookie": cookie},
        json={"SiteName": "Demo Site", "SiteInterfaceCacheTime": 60},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "参数格式错误"}


async def test_server_config_follows_the_panel(client, cookie, clean_db):
    response = client.post(
        "/api/admin/site",
        headers={"Cookie": cookie},
        json=_site_form(SiteName="Demo Site", SiteInterfaceCacheTime=60),
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}

    body = client.get("/api/server-config").json()
    assert body["SiteName"] == "Demo Site"

    # The form rebuilds SiteConfig from the body, and `strip_none` at
    # serialization drops the optional fields it did not send — the same as
    # `JSON.stringify` dropping `undefined`, so the blob has exactly the nine
    # fields the form carried. The cache time is written into ConfigFile too.
    inner = await Db.get_admin_config()
    if isinstance(inner, str):
        inner = json.loads(inner)
    site = inner["SiteConfig"]
    assert sorted(site) == sorted(_site_form(SiteName="Demo Site", SiteInterfaceCacheTime=60))
    assert site["Announcement"] == "hello"
    assert "TVBoxEnabled" not in site
    assert site["SiteInterfaceCacheTime"] == 60
    assert json.loads(inner["ConfigFile"])["cache_time"] == 60

    # `refine_config` fills the two optional fields it is asked to serve, so the
    # panel sees them as their defaults, not as absent. `/api/admin/config` wraps
    # the config as `{"Role": …, "Config": …}`.
    served = client.get("/api/admin/config", headers={"Cookie": cookie}).json()["Config"]["SiteConfig"]
    assert served["TVBoxEnabled"] is False
    assert served["TVBoxPassword"] == ""


async def test_the_panel_cache_time_reaches_the_cached_routes(client, cookie, fake_source, clean_db):
    client.post("/api/admin/site", headers={"Cookie": cookie}, json=_site_form(SiteInterfaceCacheTime=60))
    response = client.get(
        "/api/detail", params={"id": "1", "source": fake_source}, headers={"Cookie": cookie}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=60, s-maxage=60"

    # 0 falls back to the 7200 default (`getCacheTime`'s `|| 7200`).
    client.post("/api/admin/site", headers={"Cookie": cookie}, json=_site_form(SiteInterfaceCacheTime=0))
    response = client.get(
        "/api/detail", params={"id": "1", "source": fake_source}, headers={"Cookie": cookie}
    )
    assert response.headers["cache-control"] == "public, max-age=7200, s-maxage=7200"


async def test_a_config_file_cache_time_outranks_the_panel_field(client, cookie, fake_source, clean_db):
    # The panel writes 60 into both SiteConfig and ConfigFile. Replacing the
    # ConfigFile string with cache_time=30 makes the next read serve 30, so the
    # re-injection is live: while a config file carries cache_time, the panel's
    # 接口缓存时间 field is overridden on every read.
    client.post("/api/admin/site", headers={"Cookie": cookie}, json=_site_form(SiteInterfaceCacheTime=60))

    blob = await Db.get_admin_config()
    if isinstance(blob, str):
        blob = json.loads(blob)
    config_file = json.loads(blob["ConfigFile"])
    config_file["cache_time"] = 30
    response = client.post(
        "/api/admin/config_file",
        headers={"Cookie": cookie},
        json={"configFile": json.dumps(config_file, ensure_ascii=False)},
    )
    assert response.status_code == 200

    response = client.get(
        "/api/detail", params={"id": "1", "source": fake_source}, headers={"Cookie": cookie}
    )
    assert response.headers["cache-control"] == "public, max-age=30, s-maxage=30"

    # The panel's field itself is untouched in the blob; only the re-injected
    # read changed.
    inner = await Db.get_admin_config()
    if isinstance(inner, str):
        inner = json.loads(inner)
    assert inner["SiteConfig"]["SiteInterfaceCacheTime"] == 60
