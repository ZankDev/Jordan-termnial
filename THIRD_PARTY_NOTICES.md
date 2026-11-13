# Third-party notices

## God's Eye View

- Project: God's Eye View — <https://github.com/bilawalsidhu/gods-eye-view>
- Copyright (c) 2026 Bilawal Sidhu
- License: MIT

Used in: the WORLD › EVENTS MAP "GOD'S EYE" layers (`app/world/godseye.py`, `app/api/world_gx_routes.py`,
`web/godseye_layers.js`, and the NHC GIS cyclone path in `app/api/world_live_routes.py`). No source files were copied
verbatim; its keyless data methods were re-implemented in Python / vanilla JS: NOAA GFS wind (read here from the PacIOOS
ERDDAP mirror instead of GRIB2 range reads), NOAA nowCOAST WMS products (satellite IR, MRMS radar, lightning density),
the NHC/CPHC GIS MapServer cyclone method (forecast points, cone, past points, 7-day outlook), the adsb.lol regional
flight fallback, and the GNSS navigation-integrity classifier `gev-nic-nacp-v2` with gpsjam.org-style banding.

Not included: the TeleGeography submarine-cable dataset (non-commercial licence) and any other bundled God's Eye
datasets or 3D assets.

MIT License text:

```
MIT License

Copyright (c) 2026 Bilawal Sidhu

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Data sources (credited in-app under EVENTS MAP › CREDITS)

USGS, NOAA NHC/CPHC, NOAA GFS (via PacIOOS ERDDAP), NOAA/NWS nowCOAST, GDACS, NASA EONET, RainViewer, OpenSky Network,
adsb.lol (ODbL 1.0), aisstream.io (optional, `AISSTREAM_API_KEY`), CelesTrak, Esri basemap tiles.

## Browser libraries (loaded from jsDelivr on demand)

- Leaflet 1.9.4 — BSD-2-Clause
- leaflet-velocity 2.1.4 — MIT
- satellite.js 5.0.0 — MIT
