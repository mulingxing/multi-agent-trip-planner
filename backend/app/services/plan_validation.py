"""用高德POI详情核验景点，避免将模型生成的坐标直接展示在地图上。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from typing import TYPE_CHECKING, Any
import unicodedata

from ..models.schemas import Location, TripPlan, TripRequest

if TYPE_CHECKING:
    from hello_agents.tools import MCPTool


@dataclass(frozen=True)
class _VerifiedPOI:
    name: str
    address: str
    longitude: float
    latitude: float
    poi_id: str


def _compact(value: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value)).casefold()


def _city_name(value: str) -> str:
    return _compact(value).removesuffix("市")


def _name(value: str, city: str, *, simplify: bool = False) -> str:
    name = _compact(value)
    for prefix in (_compact(city).removesuffix("市") + "市", _city_name(city)):
        if prefix and name.startswith(prefix):
            name = name[len(prefix):]
            break
    if simplify:
        name = name.replace("国家湿地公园", "湿地").replace("湿地公园", "湿地")
        for suffix in ("风景名胜区", "风景区", "景区"):
            if name.endswith(suffix):
                name = name[:-len(suffix)]
                break
    return name


def _name_parts(value: str) -> tuple[str, list[str], list[str]]:
    """区分主名称、括号别名和必须保留的分馆/入口限定。"""
    normalized = unicodedata.normalize("NFKC", value)
    blocks = re.findall(r"\(([^()]*)\)", normalized)
    primary = re.sub(r"\([^()]*\)", "", normalized).strip()
    aliases, qualifiers = [], []
    for block in blocks:
        for part in re.split(r"[·•、,;/|]", block):
            part = re.sub(r"^(?:又名|别名|原名|俗称)[:：]?", "", part.strip()).strip()
            if not part:
                continue
            if re.search(r"(?:馆区|校区|园区|分馆|分院|分校|分店|[东西南北]区|[东西南北]门|入口|出口)", part):
                qualifiers.append(part)
            elif not re.search(r"(?:风景名胜区|风景区|景区)(?:内)?$|^(?:位于|位在|含|包含)", part):
                # “岳飞墓(岳王庙·西湖风景名胜区)”中的西湖只是上下文。
                if len(_compact(part)) >= 2:
                    aliases.append(part)
    return primary, aliases, qualifiers


def _hierarchy_parts(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[-—–/]", value) if part.strip()]


def _generic_terminal(value: str) -> bool:
    return bool(re.fullmatch(
        r"(?:[东西南北]{1,2}(?:门|入口|出口)|正门|大门|入口|出口|游客中心|游客服务中心|服务中心|售票处|停车场)",
        _compact(value),
    ))


def _same_name(left: str, right: str, city: str) -> bool:
    normalized = _name(left, city, simplify=True)
    return bool(normalized) and normalized == _name(right, city, simplify=True)


def _match_score(query: str, candidate: str, city: str) -> int:
    """整名优先，其次主名称，最后别名；名称主体必须相等，不猜任意子串。"""
    if _name(query, city) == _name(candidate, city):
        return 100
    if _same_name(query, candidate, city):
        return 95
    query_primary, query_aliases, qualifiers = _name_parts(query)
    candidate_primary, candidate_aliases, _ = _name_parts(candidate)
    # “博物馆(之江馆区)”不能退化为其他馆区，哪怕主名称相同。
    if any(_compact(part) not in _compact(candidate) for part in qualifiers):
        return 0
    query_parts = _hierarchy_parts(query_primary)
    candidate_parts = _hierarchy_parts(candidate_primary)
    if not query_parts or not candidate_parts:
        return 0
    if len(query_parts) > 1 and len(candidate_parts) > 1:
        # 双方给出父级时必须逐层一致，不能把“西湖-南门”匹配到“灵隐寺-南门”。
        # 允许官方名称补充外层景区，例如“西湖-岳王庙-南门”与“岳王庙-南门”。
        if any(not _same_name(left, right, city) for left, right in zip(
            reversed(query_parts[:-1]), reversed(candidate_parts[:-1]),
        )):
            return 0
    elif len(query_parts) != len(candidate_parts):
        # 单方省略父级只允许独立地名，“南门”本身不足以确认属于哪个景点。
        if _generic_terminal(query_parts[-1]) or _generic_terminal(candidate_parts[-1]):
            return 0
    query_primary = query_parts[-1]
    candidate_primary = candidate_parts[-1]
    if _same_name(query_primary, candidate_primary, city):
        return 80
    if any(_same_name(query_primary, alias, city) for alias in candidate_aliases):
        return 40
    if any(_same_name(alias, candidate_primary, city) for alias in query_aliases):
        return 30
    if any(_same_name(left, right, city) for left in query_aliases for right in candidate_aliases):
        return 20
    return 0


def _query(tool: MCPTool, tool_name: str, arguments: dict[str, str], name: str) -> dict[str, Any]:
    try:
        result = tool.run({
            "action": "call_tool", "tool_name": tool_name, "arguments": arguments,
        })
    except Exception:
        # 第三方异常可能包含带key的请求URL，不能直接传给接口或日志。
        raise ValueError(f"景点“{name}”的高德查询失败（{tool_name}）") from None

    if isinstance(result, dict):
        data = result
    elif isinstance(result, str):
        try:
            # HelloAgents MCPTool会在JSON前添加“工具...执行结果”文本。
            start = result.index("{")
            data, _ = json.JSONDecoder().raw_decode(result[start:])
        except (ValueError, json.JSONDecodeError):
            raise ValueError(f"景点“{name}”的高德查询未返回有效数据（{tool_name}）") from None
    else:
        raise ValueError(f"景点“{name}”的高德查询未返回有效数据（{tool_name}）")
    if not isinstance(data, dict) or data.get("error") or data.get("isError"):
        raise ValueError(f"景点“{name}”的高德查询失败（{tool_name}）")
    return data


def _verify_poi(name: str, city: str, tool: MCPTool, expected_poi_id: str | None = None) -> _VerifiedPOI:
    primary, _, _ = _name_parts(name)
    normalized_name = _name(primary, city, simplify=True)
    if not normalized_name or re.fullmatch(
        r"(?:.*景点[0-9一二三四五六七八九十零〇]+|景点名称|景点|著名景点|待定|待补充)",
        normalized_name,
    ):
        raise ValueError("行程包含占位景点，请重新生成真实景点后再规划")

    candidates: dict[str, tuple[int, dict[str, Any]]] = {}
    search_terms = [name]
    if primary and _compact(primary) != _compact(name):
        search_terms.append(primary)
    has_results = False
    for keywords in search_terms:
        search = _query(tool, "maps_text_search", {
            "keywords": keywords, "city": city, "citylimit": "true",
        }, name)
        pois = search.get("pois")
        if not isinstance(pois, list):
            continue
        has_results = has_results or bool(pois)
        for poi in pois:
            if not isinstance(poi, dict) or not isinstance(poi.get("name"), str) or not poi.get("id"):
                continue
            # 排除以景点命名的餐馆、酒店和停车场等附属设施。
            typecodes = str(poi.get("typecode") or "").split(";")
            if any(code.startswith(("05", "06", "07", "09", "10", "15", "16")) for code in typecodes):
                continue
            score = _match_score(name, poi["name"], city)
            poi_id = str(poi["id"])
            if score and score > candidates.get(poi_id, (0, {}))[0]:
                candidates[poi_id] = (score, poi)
        # 已找到主名称时无需再次搜索；只有别名时先补查主名称。
        if any(score >= 80 for score, _ in candidates.values()):
            break
    if not has_results:
        raise ValueError(f"未在{city}查到景点“{name}”，不能确认其真实位置")
    if not candidates:
        raise ValueError(f"未在{city}找到名称匹配的景点“{name}”，不能使用其他搜索结果代替")
    highest = max(score for score, _ in candidates.values())
    matches = [poi for score, poi in candidates.values() if score == highest]
    if len(matches) != 1:
        # 模型给出的ID仅能在同分、已匹配名称的真实搜索结果中消歧。
        identified = [poi for poi in matches if str(poi["id"]) == expected_poi_id]
        if len(identified) != 1:
            raise ValueError(f"{city}的景点“{name}”有多个同名结果，请使用更具体的景点名称")
        matches = identified
    selected = matches[0]
    poi_id = str(selected["id"])

    detail = _query(tool, "maps_search_detail", {"id": poi_id}, name)
    actual_city = detail.get("city")
    if not isinstance(actual_city, str) or not actual_city.strip():
        raise ValueError(f"无法确认景点“{name}”所属城市，不能展示其位置")
    if _city_name(actual_city) != _city_name(city):
        raise ValueError(f"景点“{name}”不属于请求城市{city}，已拒绝跨市坐标")
    actual_name = detail.get("name")
    if (
        str(detail.get("id") or "") != poi_id
        or not isinstance(actual_name, str)
        or not _match_score(name, actual_name, city)
    ):
        raise ValueError(f"景点“{name}”的高德详情与搜索结果不一致")

    coordinates = detail.get("location")
    try:
        if not isinstance(coordinates, str):
            raise ValueError
        longitude, latitude = (float(part.strip()) for part in coordinates.split(","))
        if not (
            math.isfinite(longitude) and math.isfinite(latitude)
            and -180 <= longitude <= 180 and -90 <= latitude <= 90
        ) or (longitude == 0 and latitude == 0):
            raise ValueError
    except (ValueError, TypeError):
        raise ValueError(f"高德未提供景点“{name}”的有效经纬度，不能使用模型猜测的坐标") from None

    address = detail.get("address") or selected.get("address") or ""
    if not isinstance(address, str):
        address = ""
    return _VerifiedPOI(actual_name, address, longitude, latitude, poi_id)


def verify_attraction_locations(plan: TripPlan, request: TripRequest, amap_tool: MCPTool) -> None:
    """核验全部景点后原地更新计划；失败不写入部分结果，也不保留猜测坐标。"""
    cache: dict[tuple[str, str | None], _VerifiedPOI] = {}
    updates = []
    for day in plan.days:
        for attraction in day.attractions:
            cache_key = (_name(attraction.name, request.city, simplify=True), attraction.poi_id)
            if cache_key not in cache:
                cache[cache_key] = _verify_poi(attraction.name, request.city, amap_tool, attraction.poi_id)
            updates.append((attraction, cache[cache_key]))

    for attraction, poi in updates:
        attraction.name = poi.name
        attraction.address = poi.address
        attraction.location = Location(longitude=poi.longitude, latitude=poi.latitude)
        attraction.poi_id = poi.poi_id
