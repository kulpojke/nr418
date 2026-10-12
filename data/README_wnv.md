# Sacramento–Yolo West Nile virus map extract

Downloaded: 2026-09-29

## Files

- `wnv_sacramento_yolo.geojson`: 4,556 mapped WNV-positive locations in Sacramento and Yolo counties, EPSG:4326.
- `wnv_sacramento_yolo.csv`: 7,519 location-month records after duplicate location-month entries were combined and their positive counts summed.

## Sources and processing

- Public WNV map: <https://www.fightthebite.net/media/wnv-activity-map/>
- Public VectorSurv map service used by that page: <https://mathew.vectorsurv.org/v2/arbo/>
- County boundaries: U.S. Census Bureau TIGERweb State and County layer.

The extract includes the public map's `WNVPools`, `WNVBirds`, and `WNVSentinels` layers. Points were spatially clipped to the Census boundaries for Sacramento County (GEOID 06067) and Yolo County (GEOID 06113). The `county` field is therefore inferred from point-in-polygon location rather than supplied by VectorSurv.

The CSV contains:

- `longitude`, `latitude`: mapped point coordinates;
- `county`: county inferred from the Census boundary;
- `city`, `state`: labels supplied by VectorSurv;
- `record_type`: `WNVPools`, `WNVBirds`, or `WNVSentinels`;
- `month`, `year`: temporal resolution supplied by the public endpoint; and
- `positive_count`: number of positive records at that mapped location for that month and record type.

Observed coverage in this download is 2004–2026. The service is live, so later downloads can differ.

## Important analytical limitations

- These are positive detections only. The files do not contain negative tests, unsampled locations, trap effort, or detection probability. A missing point must not be treated as confirmed WNV absence.
- Public-map coordinates may be aggregated, generalized, or otherwise differ from original surveillance locations. Confirm spatial precision with the data custodian before fine-scale analysis.
- Mosquito-pool detections, dead-bird detections, and sentinel detections represent different surveillance processes and should not automatically be treated as equivalent observations.
- For an occupancy analysis, request complete tested-sample data from the Sacramento–Yolo Mosquito & Vector Control District or VectorSurv, including positive and negative results, sampling dates, coordinate precision, mosquito species, pool size, trap effort, and relevant site identifiers.

