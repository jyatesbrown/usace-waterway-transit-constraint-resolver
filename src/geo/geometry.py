"""Query geometry in a local azimuthal-equidistant projection.

Buffers and intersections are computed in metres in an AEQD projection centred on the query, so a
nautical-mile buffer is a true distance rather than a buffer of WGS84 degrees. Reported distances are
geodesic (WGS84 ellipsoid) between the nearest points found in that projection.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

import numpy as np
import shapely
from pyproj import CRS, Geod, Transformer
from shapely import ops
from shapely.geometry import LineString, Point, Polygon, box
from shapely.geometry.base import BaseGeometry

METRES_PER_NM = 1852.0
GEOD = Geod(ellps="WGS84")
BUFFER_RESOLUTION = 32
BBOX_DENSIFY_DEGREES = 0.05

QueryType = Literal["point", "route", "bbox"]
LonLat = tuple[float, float]


@dataclass
class QueryArea:
    type: QueryType
    center: LonLat
    to_local: Transformer
    to_wgs84: Transformer
    query_geometry: BaseGeometry
    search_area: BaseGeometry
    route_line: LineString | None = None
    route_length_nm: float | None = None

    def project(self, geom: BaseGeometry) -> BaseGeometry:
        return shapely.transform(geom, lambda xy: np.column_stack(self.to_local.transform(xy[:, 0], xy[:, 1])))

    def geodesic_distance_nm(self, a: BaseGeometry, b: BaseGeometry) -> float:
        pa, pb = ops.nearest_points(a, b)
        lon1, lat1 = self.to_wgs84.transform(pa.x, pa.y)
        lon2, lat2 = self.to_wgs84.transform(pb.x, pb.y)
        _, _, metres = GEOD.inv(lon1, lat1, lon2, lat2)
        return metres / METRES_PER_NM


def _transformers(center: LonLat) -> tuple[Transformer, Transformer]:
    local = CRS.from_proj4(f"+proj=aeqd +lat_0={center[1]} +lon_0={center[0]} +datum=WGS84 +units=m +no_defs")
    return (
        Transformer.from_crs("EPSG:4326", local, always_xy=True),
        Transformer.from_crs(local, "EPSG:4326", always_xy=True),
    )


def geodesic_length_nm(points: Sequence[LonLat]) -> float:
    lons, lats = zip(*points, strict=True)
    return GEOD.line_length(lons, lats) / METRES_PER_NM


def point_area(lon: float, lat: float, radius_nm: float) -> QueryArea:
    fwd, inv = _transformers((lon, lat))
    query = Point(0.0, 0.0)
    return QueryArea("point", (lon, lat), fwd, inv, query, query.buffer(radius_nm * METRES_PER_NM, BUFFER_RESOLUTION))


def route_area(points: Sequence[LonLat], corridor_nm: float) -> QueryArea:
    """`points` are (lon, lat). Segments are densified along the geodesic before projection."""
    length_nm = geodesic_length_nm(points)
    mid_lon, mid_lat = _geodesic_midpoint(points, length_nm)
    fwd, inv = _transformers((mid_lon, mid_lat))
    dense: list[LonLat] = []
    for (lon1, lat1), (lon2, lat2) in pairwise(points):
        dense.append((lon1, lat1))
        _, _, seg_m = GEOD.inv(lon1, lat1, lon2, lat2)
        n = int(seg_m // (5 * METRES_PER_NM))
        if n:
            dense.extend(GEOD.npts(lon1, lat1, lon2, lat2, n))
    dense.append(points[-1])
    line = LineString([fwd.transform(lon, lat) for lon, lat in dense])
    corridor = line.buffer(corridor_nm * METRES_PER_NM, BUFFER_RESOLUTION)
    return QueryArea("route", (mid_lon, mid_lat), fwd, inv, line, corridor, line, length_nm)


def bbox_area(south: float, west: float, north: float, east: float) -> QueryArea:
    center = ((west + east) / 2, (south + north) / 2)
    fwd, inv = _transformers(center)
    ring = box(west, south, east, north).exterior.segmentize(BBOX_DENSIFY_DEGREES)
    poly = Polygon([fwd.transform(x, y) for x, y in ring.coords])
    return QueryArea("bbox", center, fwd, inv, poly, poly)


def _geodesic_midpoint(points: Sequence[LonLat], length_nm: float) -> LonLat:
    half = length_nm * METRES_PER_NM / 2
    walked = 0.0
    for (lon1, lat1), (lon2, lat2) in pairwise(points):
        az, _, seg = GEOD.inv(lon1, lat1, lon2, lat2)
        if walked + seg >= half and seg > 0:
            lon, lat, _ = GEOD.fwd(lon1, lat1, az, half - walked)
            return (lon, lat)
        walked += seg
    return points[0]
