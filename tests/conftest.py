import json
from pathlib import Path

import httpx
import pytest
import respx

FIX = Path(__file__).parent / "fixtures" / "usace"
NTNI = "https://ndc.ops.usace.army.mil/ords/ntni"
LPMS = "https://ndc.ops.usace.army.mil/ords/lpms/json/lock_status_report"
LOCKS = "https://services7.arcgis.com/n1YM8pTrFmm7L4hs/arcgis/rest/services/Locks/FeatureServer/0/query"
DISTRICTS = (
    "https://services7.arcgis.com/n1YM8pTrFmm7L4hs/arcgis/rest/services/usace_cw_districts/FeatureServer/0/query"
)


def load(name):
    return json.loads((FIX / name).read_text())


@pytest.fixture
def usace():
    with respx.mock(assert_all_called=False) as mock:
        notices = load("notices.json")
        notices.append(load("leaflet_list_215082.json"))
        mock.get(url__startswith=NTNI + "/json_data/notices/").mock(return_value=httpx.Response(200, json=notices))
        mock.get(url__startswith=NTNI + "/json_data/notices_geoJson/").mock(
            return_value=httpx.Response(200, json=load("notices_geoJson.json"))
        )
        for cn in (215082, 215139):
            mock.get(f"{NTNI}/leaflet_json/notice/{cn}").mock(
                return_value=httpx.Response(200, json=load(f"leaflet_notice_{cn}.json"))
            )
        mock.get(url__startswith=NTNI + "/leaflet_json/notice/").mock(
            return_value=httpx.Response(404, text=(FIX / "leaflet_notice_404.html").read_text())
        )
        mock.get(url__startswith=DISTRICTS).mock(return_value=httpx.Response(200, json=load("districts_query.json")))
        mock.get(url__startswith=LOCKS).mock(return_value=httpx.Response(200, json=load("locks.geojson")))
        mock.get(url__startswith=LPMS).mock(return_value=httpx.Response(200, json=load("lock_status_report.json")))
        yield mock
