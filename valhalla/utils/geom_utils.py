from typing import List, Sequence

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsPointXY,
    QgsProject,
)

from ..third_party.routingpy.routingpy.utils import decode_polyline5

WGS84 = QgsCoordinateReferenceSystem.fromEpsgId(4326)


def point_to_wgs84(
    point: QgsPointXY,
    own_crs: QgsCoordinateReferenceSystem,
    direction: int = QgsCoordinateTransform.TransformDirection.ForwardTransform,
) -> QgsPointXY:
    """
    Transforms the ``point`` to (``direction=ForwardTransform``) or from
    (``direction=ReverseTransform``) WGS84.
    """
    project = QgsProject.instance()
    out_point = point
    if own_crs != WGS84:
        xform = QgsCoordinateTransform(own_crs, WGS84, project)
        point_transform = xform.transform(point, direction)
        out_point = point_transform

    return out_point


def encode_polyline6(coords: Sequence[Sequence[float]]) -> str:
    """
    Encodes lng/lat coordinates (any further dimensions are ignored) with a precision of 6.
    """
    encoded = []
    prev_lat, prev_lng = 0, 0
    for lng, lat, *_ in coords:
        lat, lng = round(lat * 1e6), round(lng * 1e6)
        for delta in (lat - prev_lat, lng - prev_lng):
            delta = ~(delta << 1) if delta < 0 else delta << 1
            while delta >= 0x20:
                encoded.append(chr((0x20 | (delta & 0x1F)) + 63))
                delta >>= 5
            encoded.append(chr(delta + 63))
        prev_lat, prev_lng = lat, lng

    return "".join(encoded)


def decode_polyline(encoded: str) -> List[QgsPointXY]:
    # TODO: change the order to lnglat: the uploader used the wrong order when encoding
    return [QgsPointXY(x, y) for x, y in decode_polyline5(encoded, order="latlng")]
