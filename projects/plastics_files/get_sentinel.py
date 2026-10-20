#!/usr/bin/env python3
'''Download Sentinel-1 GRD or Sentinel-2 L2A tiles from Copernicus Data Space.

Required arguments:

    aoi       Any OGR-readable polygon dataset.
    bands     Collection band names, separated by spaces and/or commas.

The script searches the CDSE STAC catalog, selects one acquisition date, downloads
the AOI as <=2500 x 2500 pixel multiband GeoTIFF tiles through the Sentinel Hub
Process API, and builds a VRT instead of writing a physical mosaic. Sentinel-2 is
returned as FLOAT32 BOA/surface reflectance. Sentinel-1 is returned as FLOAT32
linear backscatter power. No local DN scaling is needed for either collection.
For Sentinel-2, the Process API is explicitly asked for REFLECTANCE units and
the evalscript returns those floating-point values unchanged. Reflectance is
not clipped to 0-1 and valid values can exceed 1.

Official Sentinel Hub references for Sentinel-2 reflectance and FLOAT32 output:

    https://docs.sentinel-hub.com/api/latest/data/sentinel-2-l2a/
    https://docs.sentinel-hub.com/api/latest/data/sentinel-2-l2a/examples/
    https://docs.sentinel-hub.com/api/latest/evalscript/v3/#sampletype

Authentication is read from CDSE_CLIENT_ID and CDSE_CLIENT_SECRET. Credentials
can instead be supplied with --client-id and --client-secret. If an ID is
supplied without a secret, the script prompts for the secret without echo.

Examples:

    python get_sentinel.py AOI.geojson B02 B03 B04 B8A B11 B12

    python get_sentinel.py AOI.geojson B02 B03 B04 --client-id --client-secret

    python get_sentinel.py AOI.geojson B02 B03 B04 B8A B11 B12 --resample-to 10
    
    python get_sentinel.py AOI.geojson B02,B03,B04 --date 2025-08-23

    python get_sentinel.py AOI.geojson VV VH \\
        --collection sentinel-1-grd \\
        --s1-orbit-direction DESCENDING
'''

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import requests


STAC_SEARCH_URL = 'https://stac.dataspace.copernicus.eu/v1/search'
TOKEN_URL = (
    'https://identity.dataspace.copernicus.eu/auth/realms/CDSE/'
    'protocol/openid-connect/token'
)
PROCESS_URL = 'https://sh.dataspace.copernicus.eu/process/v1'
NODATA = -9999.0
PROMPT_VALUE = '__PROMPT_FOR_VALUE__'

S2_REFLECTANCE_REFERENCES = [
    {
        'title': 'Sentinel-2 L2A data and units',
        'url': 'https://docs.sentinel-hub.com/api/latest/data/sentinel-2-l2a/',
        'relevance': (
            'REFLECTANCE is BOA reflectance computed from source DN with at '
            'least Float32 precision; DN = 10000 * REFLECTANCE.'
        ),
    },
    {
        'title': 'Sentinel-2 L2A Process API examples',
        'url': (
            'https://docs.sentinel-hub.com/api/latest/data/'
            'sentinel-2-l2a/examples/'
        ),
        'relevance': (
            'The official FLOAT32 example returns sample band values directly '
            'and describes the result as band reflectance.'
        ),
    },
    {
        'title': 'Evalscript V3 SampleType documentation',
        'url': 'https://docs.sentinel-hub.com/api/latest/evalscript/v3/#sampletype',
        'relevance': (
            'FLOAT32 stores floating-point output without AUTO scaling or '
            'integer clamping.'
        ),
    },
]

# Native resolutions are recorded in the sidecar. All requested bands are
# resampled by Sentinel Hub to the common output resolution.
S2_BAND_INFO = {
    'B01': {'native_resolution_m': 60},
    'B02': {'native_resolution_m': 10},
    'B03': {'native_resolution_m': 10},
    'B04': {'native_resolution_m': 10},
    'B05': {'native_resolution_m': 20},
    'B06': {'native_resolution_m': 20},
    'B07': {'native_resolution_m': 20},
    'B08': {'native_resolution_m': 10},
    'B8A': {'native_resolution_m': 20},
    'B09': {'native_resolution_m': 60},
    'B11': {'native_resolution_m': 20},
    'B12': {'native_resolution_m': 20},
}

S1_BAND_INFO = {
    'VV': {'native_resolution_m': 10},
    'VH': {'native_resolution_m': 10},
    'HH': {'native_resolution_m': 10},
    'HV': {'native_resolution_m': 10},
}

COLLECTIONS = {
    'sentinel-2-l2a': {
        'short_name': 'S2_L2A',
        'title': 'Copernicus Sentinel-2 L2A surface reflectance',
        'bands': S2_BAND_INFO,
        'input_units': 'REFLECTANCE',
        'output_units': 'surface_reflectance',
        'mosaicking_order': 'leastCC',
    },
    'sentinel-1-grd': {
        'short_name': 'S1_GRD',
        'title': 'Copernicus Sentinel-1 GRD calibrated backscatter',
        'bands': S1_BAND_INFO,
        'input_units': 'LINEAR_POWER',
        'output_units': 'linear_power',
        'mosaicking_order': 'mostRecent',
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            'Download Sentinel-1 GRD or Sentinel-2 L2A bands as tiled '
            'multiband FLOAT32 GeoTIFFs and build a VRT.'
        )
    )
    parser.add_argument('aoi', type=Path, help='OGR-readable polygon dataset')
    parser.add_argument(
        'bands',
        nargs='+',
        help='bands separated by spaces and/or commas (for example: B02 B03 B04)',
    )
    parser.add_argument(
        '--collection',
        choices=tuple(COLLECTIONS),
        default='sentinel-2-l2a',
        help='Copernicus collection (default: sentinel-2-l2a)',
    )
    date_group = parser.add_mutually_exclusive_group()
    date_group.add_argument(
        '--date', help='exact acquisition date to use (YYYY-MM-DD)'
    )
    date_group.add_argument(
        '--start', help='first catalog-search date (YYYY-MM-DD)'
    )
    parser.add_argument(
        '--end',
        help='last catalog-search date, inclusive (YYYY-MM-DD; defaults to today)',
    )
    parser.add_argument(
        '--days',
        type=int,
        default=90,
        help='search-window length when --start is omitted (default: 90)',
    )
    parser.add_argument(
        '--max-cloud-cover',
        type=float,
        default=20.0,
        help='preferred maximum granule cloud cover percentage (default: 20)',
    )
    parser.add_argument(
        '--resample-to',
        '--resolution',
        dest='resolution',
        type=float,
        default=10.0,
        metavar='METERS',
        help=(
            'resample and align every output band to this pixel size in metres '
            '(default: 10; --resolution is retained as an alias)'
        ),
    )
    parser.add_argument(
        '--tile-size',
        type=int,
        default=2500,
        help='maximum tile width and height in pixels (default/API limit: 2500)',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        help='output directory (default: beside the AOI, named for the date)',
    )
    parser.add_argument(
        '--overwrite',
        action='store_true',
        help='redownload existing valid tile files',
    )
    parser.add_argument(
        '--client-id',
        nargs='?',
        const=PROMPT_VALUE,
        metavar='CLIENT_ID',
        help=(
            'Copernicus OAuth client ID; omit the value to be prompted '
            '(otherwise CDSE_CLIENT_ID)'
        ),
    )
    parser.add_argument(
        '--client-secret',
        nargs='?',
        const=PROMPT_VALUE,
        metavar='CLIENT_SECRET',
        help=(
            'Copernicus OAuth client secret; omit the value for a hidden prompt '
            '(otherwise CDSE_CLIENT_SECRET)'
        ),
    )
    parser.add_argument(
        '--s1-orbit-direction',
        choices=('ASCENDING', 'DESCENDING'),
        help='Sentinel-1 orbit direction (default: either)',
    )
    parser.add_argument(
        '--s1-back-coeff',
        choices=('BETA0', 'SIGMA0_ELLIPSOID', 'GAMMA0_ELLIPSOID', 'GAMMA0_TERRAIN'),
        default='GAMMA0_ELLIPSOID',
        help='Sentinel-1 backscatter coefficient (default: GAMMA0_ELLIPSOID)',
    )
    parser.add_argument(
        '--s1-no-orthorectify',
        action='store_true',
        help='disable Sentinel-1 DEM orthorectification',
    )
    return parser.parse_args()


def normalized_bands(values: list[str], band_info: dict) -> list[str]:
    bands: list[str] = []
    for value in values:
        bands.extend(part.strip().upper() for part in value.split(',') if part.strip())
    if not bands:
        raise ValueError('At least one band is required')
    unknown = [band for band in bands if band not in band_info]
    if unknown:
        allowed = ', '.join(band_info)
        raise ValueError(f"Unsupported band(s): {', '.join(unknown)}. Allowed: {allowed}")
    if len(set(bands)) != len(bands):
        raise ValueError('The band list contains duplicates')
    return bands


def require_commands() -> None:
    missing = [
        command
        for command in ('ogr2ogr', 'gdaltransform', 'gdalinfo', 'gdalbuildvrt', 'gdalwarp')
        if shutil.which(command) is None
    ]
    if missing:
        raise RuntimeError('Missing required GDAL command(s): ' + ', '.join(missing))


def run(command: list[str], *, input_text: str | None = None) -> str:
    completed = subprocess.run(
        command,
        input=input_text,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode:
        details = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Command failed ({' '.join(command)}):\n{details}")
    return completed.stdout


def read_aoi_wgs84(path: Path) -> tuple[dict, dict]:
    if not path.exists():
        raise FileNotFoundError(path)
    output = run(
        [
            'ogr2ogr',
            '-f',
            'GeoJSON',
            '/vsistdout/',
            str(path),
            '-t_srs',
            'EPSG:4326',
            '-lco',
            'RFC7946=YES',
        ]
    )
    collection = json.loads(output)
    features = collection.get('features', [])
    if not features:
        raise ValueError(f'AOI contains no features: {path}')

    geometries = []
    for feature in features:
        geometry = feature.get('geometry')
        if not geometry or geometry.get('type') not in {'Polygon', 'MultiPolygon'}:
            raise ValueError('Every AOI feature must have Polygon or MultiPolygon geometry')
        geometries.append(geometry)

    if len(geometries) == 1:
        geometry = geometries[0]
    else:
        geometry = {'type': 'GeometryCollection', 'geometries': geometries}
    return collection, geometry


def coordinate_pairs(geometry: dict) -> list[tuple[float, float]]:
    pairs: list[tuple[float, float]] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            if value.get('type') == 'GeometryCollection':
                for child in value.get('geometries', []):
                    visit(child)
            else:
                visit(value.get('coordinates'))
        elif isinstance(value, list):
            if len(value) >= 2 and all(isinstance(v, (int, float)) for v in value[:2]):
                pairs.append((float(value[0]), float(value[1])))
            else:
                for child in value:
                    visit(child)

    visit(geometry)
    if not pairs:
        raise ValueError('Could not read coordinates from AOI geometry')
    return pairs


def choose_utm_epsg(geometry: dict) -> int:
    pairs = coordinate_pairs(geometry)
    mean_lon = sum(point[0] for point in pairs) / len(pairs)
    mean_lat = sum(point[1] for point in pairs) / len(pairs)
    zone = max(1, min(60, int(math.floor((mean_lon + 180.0) / 6.0)) + 1))
    return (32600 if mean_lat >= 0 else 32700) + zone


def transformed_bounds(geometry: dict, target_epsg: int) -> tuple[float, float, float, float]:
    pairs = coordinate_pairs(geometry)
    input_text = ''.join(f'{x:.12f} {y:.12f}\n' for x, y in pairs)
    output = run(
        [
            'gdaltransform',
            '-s_srs',
            'EPSG:4326',
            '-t_srs',
            f'EPSG:{target_epsg}',
        ],
        input_text=input_text,
    )
    transformed = []
    for line in output.splitlines():
        fields = line.split()
        if len(fields) >= 2:
            transformed.append((float(fields[0]), float(fields[1])))
    if len(transformed) != len(pairs):
        raise RuntimeError('GDAL returned an unexpected number of transformed AOI vertices')
    xs, ys = zip(*transformed)
    return min(xs), min(ys), max(xs), max(ys)


def date_window(args: argparse.Namespace) -> tuple[dt.date, dt.date]:
    try:
        if args.date:
            selected = dt.date.fromisoformat(args.date)
            return selected, selected
        end = dt.date.fromisoformat(args.end) if args.end else dt.datetime.now(dt.UTC).date()
        start = dt.date.fromisoformat(args.start) if args.start else end - dt.timedelta(days=args.days)
    except ValueError as error:
        raise ValueError('Dates must use YYYY-MM-DD') from error
    if start > end:
        raise ValueError('Search start date must not be later than the end date')
    return start, end


def request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    timeout: int = 60,
    **kwargs: object,
) -> dict:
    response = session.request(method, url, timeout=timeout, **kwargs)
    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        excerpt = response.text[:1000]
        raise RuntimeError(f'{method} {url} failed ({response.status_code}): {excerpt}') from error
    return response.json()


def search_stac(
    session: requests.Session,
    geometry: dict,
    start: dt.date,
    end: dt.date,
    collection: str,
) -> list[dict]:
    payload = {
        'collections': [collection],
        'intersects': geometry,
        'datetime': f'{start.isoformat()}T00:00:00Z/{(end + dt.timedelta(days=1)).isoformat()}T00:00:00Z',
        'limit': 100,
    }
    result = request_json(session, 'POST', STAC_SEARCH_URL, json=payload)
    features = list(result.get('features', []))
    pages = 1
    while pages < 20:
        next_link = next(
            (link for link in result.get('links', []) if link.get('rel') == 'next'),
            None,
        )
        if not next_link:
            break
        method = str(next_link.get('method', 'GET')).upper()
        href = next_link['href']
        if method == 'POST':
            result = request_json(
                session,
                'POST',
                href,
                json=next_link.get('body', payload),
            )
        else:
            result = request_json(session, 'GET', href)
        features.extend(result.get('features', []))
        pages += 1
    if pages == 20 and any(
        link.get('rel') == 'next' for link in result.get('links', [])
    ):
        raise RuntimeError('STAC search exceeded the 20-page safety limit')
    return features


def filter_collection_items(
    items: list[dict],
    collection: str,
    bands: list[str],
    s1_orbit_direction: str | None,
) -> list[dict]:
    if collection != 'sentinel-1-grd':
        return items

    requested = set(bands)
    filtered = []
    for item in items:
        properties = item.get('properties', {})
        polarizations = {
            str(value).upper() for value in properties.get('sar:polarizations', [])
        }
        if polarizations and not requested.issubset(polarizations):
            continue
        orbit_state = str(properties.get('sat:orbit_state', '')).upper()
        if s1_orbit_direction and orbit_state and orbit_state != s1_orbit_direction:
            continue
        instrument_mode = str(properties.get('sar:instrument_mode', '')).upper()
        if instrument_mode and instrument_mode != 'IW':
            continue
        filtered.append(item)
    return filtered


def item_date(item: dict) -> dt.date:
    timestamp = item.get('properties', {}).get('datetime')
    if not timestamp:
        raise ValueError(f"STAC item {item.get('id', '<unknown>')} has no datetime")
    return dt.datetime.fromisoformat(timestamp.replace('Z', '+00:00')).date()


def cloud_cover(item: dict) -> float:
    value = item.get('properties', {}).get('eo:cloud_cover')
    return float(value) if value is not None else 100.0


def choose_acquisition(
    items: list[dict],
    preferred_cloud_cover: float,
    exact_date: bool,
    collection: str,
) -> tuple[dt.date, list[dict], dict]:
    if not items:
        raise RuntimeError(f'No compatible {collection} scenes intersect the AOI in the search interval')

    groups: dict[dt.date, list[dict]] = {}
    for item in items:
        groups.setdefault(item_date(item), []).append(item)

    if exact_date:
        selected_date = next(iter(groups))
        selected = groups[selected_date]
        method = 'user-specified date'
    elif collection == 'sentinel-2-l2a':
        # A large AOI commonly intersects several MGRS granules. Favor dates with
        # the largest intersecting granule count before comparing cloud cover so
        # a clear sliver at the AOI edge does not beat a complete acquisition.
        max_count = max(len(group) for group in groups.values())
        complete = [(date, group) for date, group in groups.items() if len(group) == max_count]
        preferred = [
            (date, group)
            for date, group in complete
            if max(cloud_cover(item) for item in group) <= preferred_cloud_cover
        ]
        candidates = preferred or complete
        selected_date, selected = min(
            candidates,
            key=lambda pair: (
                max(cloud_cover(item) for item in pair[1]),
                sum(cloud_cover(item) for item in pair[1]) / len(pair[1]),
                -pair[0].toordinal(),
            ),
        )
        method = (
            'maximum intersecting-granule count, then preferred maximum cloud cover, '
            'then lowest cloud cover, then newest date'
        )
    else:
        max_count = max(len(group) for group in groups.values())
        complete = [(date, group) for date, group in groups.items() if len(group) == max_count]
        selected_date, selected = max(complete, key=lambda pair: pair[0])
        method = 'maximum intersecting-granule count, then newest date'

    summary = {
        'method': method,
        'intersecting_item_count': len(selected),
    }
    if collection == 'sentinel-2-l2a':
        covers = [cloud_cover(item) for item in selected]
        summary.update(
            {
                'maximum_item_cloud_cover_percent': max(covers),
                'mean_item_cloud_cover_percent': sum(covers) / len(covers),
                'preferred_maximum_cloud_cover_percent': preferred_cloud_cover,
            }
        )
    return selected_date, selected, summary


def configure_credentials(args: argparse.Namespace) -> None:
    '''Resolve credentials for this process without writing them to disk.'''
    prompt_for_id = args.client_id == PROMPT_VALUE
    prompt_for_secret = args.client_secret == PROMPT_VALUE

    client_id = None if prompt_for_id else args.client_id
    client_secret = None if prompt_for_secret else args.client_secret
    client_id = client_id or os.environ.get('CDSE_CLIENT_ID')
    client_secret = client_secret or os.environ.get('CDSE_CLIENT_SECRET')

    if prompt_for_id:
        client_id = input('CDSE client ID: ').strip()
    if prompt_for_secret:
        client_secret = getpass.getpass('CDSE client secret: ')

    if client_id and not client_secret:
        client_secret = getpass.getpass('CDSE client secret: ')
    if client_secret and not client_id:
        client_id = input('CDSE client ID: ').strip()
    if not client_id or not client_secret:
        raise ValueError('Both the CDSE client ID and client secret are required')
    if client_id and client_secret:
        # os.environ is portable across Windows, macOS, and Linux. The values
        # are inherited by child processes but cannot alter the parent shell.
        os.environ['CDSE_CLIENT_ID'] = client_id
        os.environ['CDSE_CLIENT_SECRET'] = client_secret


def oauth_token(session: requests.Session) -> str:
    client_id = os.environ.get('CDSE_CLIENT_ID')
    client_secret = os.environ.get('CDSE_CLIENT_SECRET')
    if not client_id or not client_secret:
        raise RuntimeError(
            'Set CDSE_CLIENT_ID and CDSE_CLIENT_SECRET to a Copernicus Sentinel Hub '
            'OAuth client before downloading'
        )
    response = session.post(
        TOKEN_URL,
        data={
            'grant_type': 'client_credentials',
            'client_id': client_id,
            'client_secret': client_secret,
        },
        timeout=60,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as error:
        raise RuntimeError(
            f'OAuth token request failed ({response.status_code}): {response.text[:1000]}'
        ) from error
    return response.json()['access_token']


def aligned_grid(
    bounds: tuple[float, float, float, float], resolution: float
) -> tuple[float, float, float, float, int, int]:
    xmin, ymin, xmax, ymax = bounds
    xmin = math.floor(xmin / resolution) * resolution
    ymin = math.floor(ymin / resolution) * resolution
    xmax = math.ceil(xmax / resolution) * resolution
    ymax = math.ceil(ymax / resolution) * resolution
    width = int(round((xmax - xmin) / resolution))
    height = int(round((ymax - ymin) / resolution))
    return xmin, ymin, xmax, ymax, width, height


def tile_grid(
    grid: tuple[float, float, float, float, int, int],
    resolution: float,
    tile_size: int,
) -> list[dict]:
    xmin, ymin, xmax, ymax, width, height = grid
    del ymin, xmax
    tiles = []
    rows = math.ceil(height / tile_size)
    columns = math.ceil(width / tile_size)
    for row in range(rows):
        pixel_y = row * tile_size
        tile_height = min(tile_size, height - pixel_y)
        top = ymax - pixel_y * resolution
        bottom = top - tile_height * resolution
        for column in range(columns):
            pixel_x = column * tile_size
            tile_width = min(tile_size, width - pixel_x)
            left = xmin + pixel_x * resolution
            right = left + tile_width * resolution
            tiles.append(
                {
                    'row': row,
                    'column': column,
                    'width': tile_width,
                    'height': tile_height,
                    'bbox': [left, bottom, right, top],
                    'filename': f'tile_r{row:03d}_c{column:03d}.tif',
                }
            )
    return tiles


def evalscript(bands: list[str], input_units: str) -> str:
    # Sentinel-2 uses input_units='REFLECTANCE'. Sentinel Hub converts the
    # source DN to BOA reflectance before evaluatePixel(), and FLOAT32 writes
    # the returned values without the AUTO [0,1] -> [0,255] display stretch.
    # Consequently, dividing these outputs by 10,000 would scale them twice.
    # See S2_REFLECTANCE_REFERENCES above. Reflectance is not guaranteed to lie
    # in [0,1]; the official S2L2A documentation notes values can exceed 1.
    input_bands = json.dumps(bands + ['dataMask'])
    valid_values = ', '.join(f'sample.{band}' for band in bands)
    nodata_values = ', '.join(str(NODATA) for _ in bands)
    return f'''//VERSION=3
function setup() {{
  return {{
    input: [{{ bands: {input_bands}, units: '{input_units}' }}],
    output: {{
      id: 'default',
      bands: {len(bands)},
      sampleType: 'FLOAT32',
      nodataValue: {NODATA}
    }}
  }};
}}

function evaluatePixel(sample) {{
  if (sample.dataMask === 0) return [{nodata_values}];
  return [{valid_values}];
}}
'''


def process_payload(
    tile: dict,
    target_epsg: int,
    selected_date: dt.date,
    bands: list[str],
    collection: str,
    profile: dict,
    args: argparse.Namespace,
) -> dict:
    next_date = selected_date + dt.timedelta(days=1)
    data_filter = {
        'timeRange': {
            'from': f'{selected_date.isoformat()}T00:00:00Z',
            'to': f'{next_date.isoformat()}T00:00:00Z',
        },
        'mosaickingOrder': profile['mosaicking_order'],
    }
    if collection == 'sentinel-2-l2a':
        # Scene selection has already happened through STAC. Keeping this at 100
        # prevents a cloudy edge granule from being dropped and creating a gap.
        data_filter['maxCloudCoverage'] = 100
        processing = {'upsampling': 'BILINEAR', 'downsampling': 'BILINEAR'}
    else:
        data_filter.update({'resolution': 'HIGH', 'acquisitionMode': 'IW'})
        if args.s1_orbit_direction:
            data_filter['orbitDirection'] = args.s1_orbit_direction
        if 'VH' in bands:
            data_filter['polarization'] = 'DV'
        elif 'HV' in bands:
            data_filter['polarization'] = 'DH'
        processing = {
            'upsampling': 'BILINEAR',
            'backCoeff': args.s1_back_coeff,
            'orthorectify': not args.s1_no_orthorectify,
            'demInstance': 'COPERNICUS_30',
        }
    return {
        'input': {
            'bounds': {
                'bbox': tile['bbox'],
                'properties': {
                    'crs': f'http://www.opengis.net/def/crs/EPSG/0/{target_epsg}'
                },
            },
            'data': [
                {
                    'type': collection,
                    'dataFilter': data_filter,
                    'processing': processing,
                }
            ],
        },
        'output': {
            'width': tile['width'],
            'height': tile['height'],
            'responses': [
                {'identifier': 'default', 'format': {'type': 'image/tiff'}}
            ],
        },
        'evalscript': evalscript(bands, profile['input_units']),
    }


def valid_raster(path: Path, expected_bands: int) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        details = json.loads(run(['gdalinfo', '-json', str(path)]))
    except (RuntimeError, json.JSONDecodeError):
        return False
    return len(details.get('bands', [])) == expected_bands


def download_tile(
    session: requests.Session,
    token: str,
    payload: dict,
    destination: Path,
    expected_bands: int,
) -> tuple[str, bool]:
    temporary = destination.with_suffix(destination.suffix + '.part')
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'image/tiff'}
    transient_attempts = 0
    token_refreshed = False
    while True:
        try:
            response = session.post(
                PROCESS_URL,
                headers=headers,
                json=payload,
                timeout=(30, 600),  # (connect, read)
            )
        except (
            requests.exceptions.ChunkedEncodingError,
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ContentDecodingError,
        ) as error:
            if transient_attempts == 5:
                raise RuntimeError(
                    f'Process API request failed after retries: {error}'
                ) from error
            transient_attempts += 1
            delay = min(2**transient_attempts, 60)
            print(
                f'  network error ({type(error).__name__}); '
                f'retry {transient_attempts}/5 in {delay} seconds',
                flush=True,
            )
            time.sleep(delay)
            continue

        if response.ok:
            temporary.write_bytes(response.content)
            if not valid_raster(temporary, expected_bands):
                temporary.unlink(missing_ok=True)
                # Treat a truncated/corrupt TIFF as transient too
                if transient_attempts == 5:
                    raise RuntimeError('Process API response was not a readable TIFF')
                transient_attempts += 1
                delay = min(2**transient_attempts, 60)
                print(
                    f'  unreadable TIFF; retry {transient_attempts}/5 in {delay} seconds',
                    flush=True,
                )
                time.sleep(delay)
                continue
            temporary.replace(destination)
            return token, token_refreshed
        if response.status_code == 401 and not token_refreshed:
            print('  access token expired; refreshing and retrying', flush=True)
            token = oauth_token(session)
            headers['Authorization'] = f'Bearer {token}'
            token_refreshed = True
            continue
        if response.status_code not in {429, 500, 502, 503, 504} or transient_attempts == 5:
            raise RuntimeError(
                f'Process API request failed ({response.status_code}): {response.text[:2000]}'
            )
        transient_attempts += 1
        delay = int(response.headers.get('Retry-After', 2**transient_attempts))
        print(f'  retrying after HTTP {response.status_code} in {delay} seconds', flush=True)
        time.sleep(delay)

def build_vrts(
    tile_paths: list[Path],
    aoi: Path,
    tiles_vrt: Path,
    final_vrt: Path,
    resolution: float,
) -> None:
    run(
        [
            'gdalbuildvrt',
            '-overwrite',
            '-srcnodata',
            str(NODATA),
            '-vrtnodata',
            str(NODATA),
            str(tiles_vrt),
            *[str(path) for path in tile_paths],
        ]
    )
    run(
        [
            'gdalwarp',
            '-overwrite',
            '-of',
            'VRT',
            '-cutline',
            str(aoi),
            '-crop_to_cutline',
            '-tr',
            str(resolution),
            str(resolution),
            '-tap',
            '-r',
            'near',
            '-srcnodata',
            str(NODATA),
            '-dstnodata',
            str(NODATA),
            str(tiles_vrt),
            str(final_vrt),
        ]
    )


def label_vrt_bands(path: Path, bands: list[str], profile: dict, band_units: str) -> None:
    tree = ET.parse(path)
    root = tree.getroot()
    raster_bands = root.findall('./VRTRasterBand')
    if len(raster_bands) != len(bands):
        raise RuntimeError(
            f'VRT has {len(raster_bands)} bands but {len(bands)} were requested'
        )
    for raster_band, name in zip(raster_bands, bands):
        description = raster_band.find('Description')
        if description is None:
            description = ET.SubElement(raster_band, 'Description')
        description.text = name
        metadata = raster_band.find('Metadata')
        if metadata is None:
            metadata = ET.SubElement(raster_band, 'Metadata')
        fields = {
            'BAND_NAME': name,
            'UNITS': band_units,
            'NATIVE_RESOLUTION_M': str(profile['bands'][name]['native_resolution_m']),
        }
        for key, value in fields.items():
            item = ET.SubElement(metadata, 'MDI', key=key)
            item.text = value
    ET.indent(tree, space='  ')
    tree.write(path, encoding='UTF-8', xml_declaration=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def relative_to_output(path: Path, output_dir: Path) -> str:
    try:
        return str(path.relative_to(output_dir))
    except ValueError:
        return str(path)


def main() -> int:
    args = parse_args()
    require_commands()
    collection = args.collection
    profile = COLLECTIONS[collection]
    bands = normalized_bands(args.bands, profile['bands'])
    if args.resolution <= 0:
        raise ValueError('--resolution must be positive')
    if not 1 <= args.tile_size <= 2500:
        raise ValueError('--tile-size must be between 1 and 2500')
    if not 0 <= args.max_cloud_cover <= 100:
        raise ValueError('--max-cloud-cover must be between 0 and 100')
    if args.date and args.end:
        raise ValueError('--end cannot be combined with --date')
    if collection == 'sentinel-1-grd':
        if ({'VV', 'VH'} & set(bands)) and ({'HH', 'HV'} & set(bands)):
            raise ValueError('A Sentinel-1 request cannot mix VV/VH with HH/HV polarization families')
        if args.s1_back_coeff == 'GAMMA0_TERRAIN' and args.s1_no_orthorectify:
            raise ValueError('GAMMA0_TERRAIN requires Sentinel-1 orthorectification')
    elif args.s1_orbit_direction or args.s1_no_orthorectify or args.s1_back_coeff != 'GAMMA0_ELLIPSOID':
        raise ValueError('--s1-* options can only be used with --collection sentinel-1-grd')

    aoi = args.aoi.expanduser().resolve()
    _, geometry = read_aoi_wgs84(aoi)
    start, end = date_window(args)
    print(f'Searching {collection} from {start} through {end} ...', flush=True)

    session = requests.Session()
    items = search_stac(session, geometry, start, end, collection)
    items = filter_collection_items(
        items, collection, bands, args.s1_orbit_direction
    )
    if args.date:
        items = [item for item in items if item_date(item) == start]
    selected_date, selected_items, selection = choose_acquisition(
        items,
        args.max_cloud_cover,
        exact_date=bool(args.date),
        collection=collection,
    )
    message = f'Selected {selected_date}: {len(selected_items)} intersecting item(s)'
    if collection == 'sentinel-2-l2a':
        message += f", maximum cloud cover {selection['maximum_item_cloud_cover_percent']:.1f}%"
    print(message, flush=True)

    target_epsg = choose_utm_epsg(geometry)
    bounds = transformed_bounds(geometry, target_epsg)
    grid = aligned_grid(bounds, args.resolution)
    tiles = tile_grid(grid, args.resolution, args.tile_size)
    output_dir = (
        args.output_dir.expanduser().resolve()
        if args.output_dir
        else aoi.parent / f"{aoi.stem}_{profile['short_name']}_{selected_date.isoformat()}"
    )
    tile_dir = output_dir / 'tiles'
    tile_dir.mkdir(parents=True, exist_ok=True)
    print(f'Output directory: {output_dir}', flush=True)
    print(f'Tile directory: {tile_dir}', flush=True)
    print(
        f'Output grid EPSG:{target_epsg}: {grid[4]} x {grid[5]} pixels; '
        f'{len(tiles)} tile request(s)',
        flush=True,
    )

    configure_credentials(args)
    token = oauth_token(session)
    token_time = time.monotonic()
    tile_paths = []
    for number, tile in enumerate(tiles, start=1):
        destination = tile_dir / tile['filename']
        tile_paths.append(destination)
        if not args.overwrite and valid_raster(destination, len(bands)):
            print(f'[{number}/{len(tiles)}] keeping {destination.name}', flush=True)
            continue
        if time.monotonic() - token_time > 50 * 60:
            token = oauth_token(session)
            token_time = time.monotonic()
        print(f'[{number}/{len(tiles)}] downloading {destination.name}', flush=True)
        payload = process_payload(
            tile, target_epsg, selected_date, bands, collection, profile, args
        )
        token, token_refreshed = download_tile(
            session, token, payload, destination, len(bands)
        )
        if token_refreshed:
            token_time = time.monotonic()

    stem = f"{profile['short_name']}_{selected_date.isoformat()}_{args.resolution:g}m"
    tiles_vrt = output_dir / f'{stem}_tiles.vrt'
    final_vrt = output_dir / f'{stem}.vrt'
    build_vrts(tile_paths, aoi, tiles_vrt, final_vrt, args.resolution)
    band_units = profile['output_units']
    if collection == 'sentinel-1-grd':
        band_units = f'linear_power_{args.s1_back_coeff.lower()}'
    label_vrt_bands(tiles_vrt, bands, profile, band_units)
    label_vrt_bands(final_vrt, bands, profile, band_units)

    metadata_path = output_dir / f'{stem}.metadata.json'
    metadata = {
        'schema_version': 1,
        'title': profile['title'],
        'collection': collection,
        'acquisition_date': selected_date.isoformat(),
        'selection': selection,
        'stac_items': [
            {
                'id': item.get('id'),
                'datetime': item.get('properties', {}).get('datetime'),
                'cloud_cover_percent': item.get('properties', {}).get('eo:cloud_cover'),
                'orbit_state': item.get('properties', {}).get('sat:orbit_state'),
                'polarizations': item.get('properties', {}).get('sar:polarizations'),
            }
            for item in selected_items
        ],
        'aoi': {
            'path': str(aoi),
            'sha256': sha256(aoi),
            'query_geometry_crs': 'EPSG:4326',
        },
        'bands': [
            {
                'output_index': index,
                'name': band,
                'units': band_units,
                'data_type': 'Float32',
                **profile['bands'][band],
            }
            for index, band in enumerate(bands, start=1)
        ],
        'output_grid': {
            'crs': f'EPSG:{target_epsg}',
            'resolution_m': args.resolution,
            'width_pixels_before_cutline': grid[4],
            'height_pixels_before_cutline': grid[5],
            'nodata': NODATA,
            'resampling': 'bilinear in Sentinel Hub; nearest-neighbor VRT cutline',
        },
        'processing': {
            'api': 'Copernicus Data Space Sentinel Hub Process API',
            'process_url': PROCESS_URL,
            'sample_type': 'FLOAT32',
            'input_units': profile['input_units'],
            'dn_scaling_applied_locally': False,
            'mosaicking_order_within_selected_date': profile['mosaicking_order'],
            'sentinel_1': (
                {
                    'orbit_direction': args.s1_orbit_direction or 'either',
                    'acquisition_mode': 'IW',
                    'resolution': 'HIGH',
                    'backscatter_coefficient': args.s1_back_coeff,
                    'orthorectified': not args.s1_no_orthorectify,
                    'dem': 'COPERNICUS_30' if not args.s1_no_orthorectify else None,
                }
                if collection == 'sentinel-1-grd'
                else None
            ),
        },
        'references': (
            S2_REFLECTANCE_REFERENCES if collection == 'sentinel-2-l2a' else []
        ),
        'outputs': {
            'vrt': relative_to_output(final_vrt, output_dir),
            'tile_mosaic_vrt': relative_to_output(tiles_vrt, output_dir),
            'tiles': [
                {
                    **tile,
                    'path': relative_to_output(tile_dir / tile['filename'], output_dir),
                }
                for tile in tiles
            ],
        },
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')

    print(f'Wrote VRT: {final_vrt}')
    print(f'Wrote metadata: {metadata_path}')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (FileNotFoundError, RuntimeError, ValueError) as error:
        print(f'error: {error}', file=sys.stderr)
        raise SystemExit(1)
