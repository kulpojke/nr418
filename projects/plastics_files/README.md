# Copernicus Sentinel downloader

`get_sentinel.py` downloads Sentinel-2 L2A or Sentinel-1 GRD imagery from the
Copernicus Data Space Sentinel Hub Process API.

The script:

- reads a polygon from any OGR-readable vector file;
- searches the Copernicus STAC catalog for intersecting acquisitions;
- downloads multiband `Float32` GeoTIFF tiles no larger than 2,500 × 2,500
  pixels;
- resamples every requested band onto one aligned output grid;
- builds a clipped VRT instead of writing a physical mosaic; and
- writes a JSON metadata sidecar documenting band order, units, source items,
  processing settings, and tile layout.

## Requirements

- Python 3.10 or newer
- [`requests`](https://requests.readthedocs.io/)
- GDAL command-line programs: `ogr2ogr`, `gdaltransform`, `gdalinfo`,
  `gdalbuildvrt`, and `gdalwarp`
- A Copernicus Data Space account and Sentinel Hub OAuth client

If these dependencies are not already available in the active Conda
environment:

```bash
conda install -c conda-forge requests gdal
```

No `pystac` package is required. Catalog searches and Process API requests are
made directly with `requests`.

## Create Sentinel Hub credentials

1. Sign in through the
   [Copernicus Data Space Ecosystem Dashboard](https://dataspace.copernicus.eu/copernicus-data-space-ecosystem-dashboard).
2. Open the **Sentinel Hub Services Dashboard**.
3. Open **User Settings** and find **OAuth clients**.
4. Select **Create** and give the client a descriptive name, such as
   `nr418-download`.
5. Select **Client Credentials** as the grant or flow type. This command-line
   script is not a single-page application, so do not enable the SPA option.
6. Select an expiration date, or explicitly choose a non-expiring client if
   appropriate.
7. Create the client and copy both the **client ID** and **client secret**.

The client secret is displayed only once. If it is lost, create a replacement
OAuth client. Do not add credentials to this repository or paste them into the
metadata file.

### Recommended credential entry

Pass both credential flags without values:

```bash
python get_sentinel.py AOI.geojson B02 B03 B04 \
  --client-id --client-secret
```

The script prompts visibly for the client ID and uses a hidden prompt for the
secret. Neither credential value is included in shell history, logs, output
filenames, or the metadata sidecar.

Existing `CDSE_CLIENT_ID` and `CDSE_CLIENT_SECRET` environment variables are
also supported. Avoid placing the secret directly after `--client-secret`
because command arguments can appear in shell history and the operating-system
process list.

## Basic usage

```text
python get_sentinel.py AOI BANDS... [OPTIONS]
```

- `AOI` is an OGR-readable polygon dataset such as GeoJSON, GeoPackage, or
  Shapefile. All AOI features must be polygons or multipolygons.
- `BANDS` is a space- or comma-separated list. Output band order is exactly the
  order supplied here.

Run `python get_sentinel.py --help` for the complete option list.

## Sentinel-2 examples

The default collection is `sentinel-2-l2a`. Values are returned directly as
bottom-of-atmosphere surface reflectance in `Float32`; no local DN scaling or
offset is applied.

Download bands used by the plastics workflow and align them to a 10 m grid:

```bash
python get_sentinel.py AOI.geojson \
  B02 B03 B04 B05 B06 B07 B8A B11 B12 \
  --resample-to 10 \
  --client-id --client-secret
```

Download an exact acquisition date:

```bash
python get_sentinel.py AOI.geojson B02 B03 B04 B8A B11 B12 \
  --date 2025-08-23 \
  --resample-to 10 \
  --client-id --client-secret
```

Search a particular date interval:

```bash
python get_sentinel.py AOI.geojson B02 B03 B04 \
  --start 2025-06-01 \
  --end 2025-09-30 \
  --max-cloud-cover 10 \
  --client-id --client-secret
```

`--max-cloud-cover` is a preferred scene-selection threshold, not a strict
failure threshold. If no date with full intersecting-granule coverage satisfies
it, the script selects the lowest-cloud full-coverage date available and prints
the selected cloud percentage. Cloud cover is granule-level metadata and does
not guarantee a cloud-free AOI.

Valid Sentinel-2 reflectance bands are:

```text
B01 B02 B03 B04 B05 B06 B07 B08 B8A B09 B11 B12
```

### Sentinel-2 reflectance scaling

Do **not** divide Sentinel-2 values downloaded by this script by 10,000. The
script explicitly requests `REFLECTANCE` input units and writes the returned
values directly to a `Float32` GeoTIFF. Sentinel Hub performs the conversion
from the source digital numbers before the values reach the evalscript.

The Sentinel Hub documentation gives the relationship:

```text
DN = 10000 * REFLECTANCE
```

That relationship is relevant when the requested input unit is `DN`. This
script requests `REFLECTANCE`, so applying `/ 10000` afterward would scale the
data a second time and produce values that are too small.

The script also specifies `sampleType: FLOAT32`. It does not use the default
`AUTO` sample type, which is intended for display imagery and stretches
values from `[0,1]` to `[0,255]`. The official Sentinel-2 L2A example likewise
returns the band samples unchanged for its `FLOAT32` reflectance output.

Reflectance is unitless and commonly falls between 0 and 1, but 1 is not a
hard upper bound: Sentinel Hub notes that valid Sentinel-2 reflectance can
exceed 1. With Sentinel Hub's default value harmonization, negative reflectance
is clamped to zero. The script does not clip values above 1.

If an ML model requires inputs numerically constrained to `[0,1]`, dividing
these reflectance values by 10,000 is still incorrect. Clipping values above 1
or applying a training-set normalization is a separate modeling decision and
should be documented because it discards or transforms valid reflectance.

Official references:

- [Sentinel-2 L2A data and units](https://docs.sentinel-hub.com/api/latest/data/sentinel-2-l2a/):
  defines BOA reflectance, the DN-to-reflectance relationship, Float32
  precision for non-DN units, harmonization, and values above 1.
- [Sentinel-2 L2A Process API examples](https://docs.sentinel-hub.com/api/latest/data/sentinel-2-l2a/examples/):
  includes a `FLOAT32` output that returns the input band samples directly as
  reflectance.
- [Evalscript V3 `SampleType` documentation](https://docs.sentinel-hub.com/api/latest/evalscript/v3/#sampletype):
  distinguishes `FLOAT32` output from the automatic `[0,1]` to `[0,255]`
  display stretch used by `AUTO`.

## Sentinel-1 examples

Select the Sentinel-1 GRD collection explicitly:

```bash
python get_sentinel.py AOI.geojson VV VH \
  --collection sentinel-1-grd \
  --resample-to 10 \
  --client-id --client-secret
```

Restrict the orbit direction:

```bash
python get_sentinel.py AOI.geojson VV VH \
  --collection sentinel-1-grd \
  --s1-orbit-direction DESCENDING \
  --date 2025-08-23 \
  --resample-to 10 \
  --client-id --client-secret
```

Sentinel-1 defaults to:

- IW acquisition mode;
- high resolution;
- `GAMMA0_ELLIPSOID` linear power;
- Copernicus 30 m DEM orthorectification; and
- no speckle filter.

Request radiometric terrain correction with:

```bash
--s1-back-coeff GAMMA0_TERRAIN
```

Other supported coefficients are `BETA0`, `SIGMA0_ELLIPSOID`, and
`GAMMA0_ELLIPSOID`. Supported polarization bands are `VV`, `VH`, `HH`, and
`HV`. VV/VH bands cannot be mixed with HH/HV bands in one request.

Sentinel-1 output is linear backscatter power, not decibels. For valid positive
pixels, conversion to dB is:

```text
backscatter_db = 10 * log10(backscatter_linear)
```

## Resampling and alignment

Use `--resample-to METERS` to choose the common output pixel size:

```bash
--resample-to 10
```

All requested bands in every tile have the same CRS, extent, dimensions, pixel
size, and pixel origin. The default is 10 m. `--resolution` remains available
as a compatibility alias.

Sentinel-2 bands and Sentinel-1 backscatter are resampled with bilinear
interpolation by Sentinel Hub. The final VRT applies the AOI cutline using
nearest-neighbor grid-preserving behavior.

## Date selection

Without `--date` or `--start`, the script searches the 90 days ending today.
Change that period with `--days`, or provide `--start` and optionally `--end`.

- Sentinel-2 selection favors dates with the greatest intersecting-granule
  count, then the cloud-cover preference, lower cloud cover, and recency.
- Sentinel-1 selection favors dates with the greatest intersecting-granule
  count and then the most recent date.
- `--date YYYY-MM-DD` bypasses automatic date ranking.

## Outputs

The default output directory is created beside the AOI and includes the
collection and selected date, for example:

```text
AOI_S2_L2A_2025-08-23/
├── tiles/
│   ├── tile_r000_c000.tif
│   ├── tile_r000_c001.tif
│   └── ...
├── S2_L2A_2025-08-23_10m_tiles.vrt
├── S2_L2A_2025-08-23_10m.vrt
└── S2_L2A_2025-08-23_10m.metadata.json
```

- Each tile is a multiband `Float32` GeoTIFF.
- `*_tiles.vrt` virtually mosaics the rectangular download tiles.
- The main `.vrt` applies the AOI cutline and is the normal analysis input.
- The JSON sidecar records the one-based output index and name of every band,
  along with units, acquisition metadata, CRS, resolution, NoData value, and
  tile paths.

Keep the VRT files and `tiles` directory together because VRTs reference their
source files rather than copying pixel data. Existing valid tiles are reused
when a download is restarted. Use `--overwrite` to force them to be downloaded
again.

## Troubleshooting

### Credentials are reported missing

Use interactive credential entry:

```bash
python get_sentinel.py AOI.geojson B02 B03 B04 \
  --client-id --client-secret
```

If using environment variables, verify their presence without printing their
values:

```bash
python -c 'import os; print("ID set:", bool(os.getenv("CDSE_CLIENT_ID"))); print("Secret set:", bool(os.getenv("CDSE_CLIENT_SECRET")))'
```

Both results must be `True` in the same shell or application that launches the
downloader.

### Large AOIs produce many requests

The synchronous Process API limits output tiles to 2,500 × 2,500 pixels. The
script divides larger output grids automatically and reports the request count
before downloading. More bands, finer resolution, Sentinel-1 orthorectification,
and terrain correction consume additional processing units.

### Interrupted downloads

Run the same command again. Complete readable tiles are retained, while missing
or invalid tiles are requested again. Do not use `--overwrite` when resuming.
If an access token expires during a long run, the script automatically obtains
a new token and retries the current tile once.
