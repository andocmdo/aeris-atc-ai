# Aeris — Repository Overview

**Aeris** (v0.8.3) is a real-time 3D flight tracking web app. It renders live ADS-B air traffic on a 3D WebGL map with altitude-aware color coding (cyan = low altitude → gold = high altitude). A live demo exists at aeris.edbn.me.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | Next.js 16 (App Router, Turbopack) |
| Language | TypeScript |
| Styling | Tailwind CSS v4 (glassmorphism dark theme) |
| Map | MapLibre GL JS v5 |
| 3D WebGL | Deck.gl 9 (ScenegraphLayer, IconLayer, PathLayer) |
| 3D Models | GLB files via luma.gl + loaders.gl |
| Animation | Motion (Framer Motion v12) |
| UI Primitives | Radix UI (Slider, Tabs), cmdk, Sonner, Lucide |
| Package Manager | pnpm |
| Testing | Node.js built-in test runner via tsx |

---

## Data Sources

- **Flight data**: 3-tier fallback — airplanes.live → adsb.lol → OpenSky Network
- **ATC audio**: LiveATC.net Icecast streams (proxied server-side)
- **Airspace tiles**: OpenAIP vector tiles (optional API key)
- **Weather**: METAR/TAF from AVWX
- **Aircraft photos**: Jetphotos (proxied)
- **Airport photos**: Separate proxy

---

## Architecture

```
src/
├── app/
│   ├── page.tsx               Entry — renders <FlightTracker />
│   └── api/
│       ├── flights/           adsb.lol reverse proxy + trace endpoint
│       ├── atc/               stream proxy + feeds list
│       ├── airspace-tiles/    OpenAIP tile proxy
│       ├── weather/           METAR + TAF endpoints
│       ├── weather-tiles/     Weather radar tiles
│       ├── airport-photo/     Airport photo proxy
│       ├── aircraft-photos/   Jetphotos proxy
│       └── routes/            Flight route lookup
├── components/
│   ├── flight-tracker.tsx     Main orchestrator — state, camera, layers, UI
│   ├── map/                   WebGL rendering, camera controllers, trail system,
│   │                          aircraft model mapping + 3D layers
│   └── ui/                    Control panel, flight card, airport info card,
│                              ATC player bar, FPV HUD, status bar
├── hooks/
│   ├── use-flights.ts         Adaptive polling (30s–5min, credit-aware)
│   ├── use-trail-system.ts    Trail accumulation + Catmull-Rom spline smoothing
│   ├── use-atc-stream.ts      LiveATC audio stream management
│   ├── use-airport-board.ts   Arrivals/departures board
│   └── use-settings.tsx       Settings context (localStorage-backed)
└── lib/
    ├── flight-api-client.ts   3-tier ADS-B client chain
    ├── opensky.ts             OpenSky fallback client
    ├── atc-feeds.ts           ATC feed database + allowlist
    ├── trails/                Trail data pipeline (fetch, parse, store, render)
    └── airports/cities/…      Static aviation reference data
```

---

## Current Features Relevant to ATC Safety

The app already has:
- **Live ATC audio streaming** — proxied Icecast streams from LiveATC.net, UI player bar with spectrum/waveform display
- **Real-time flight positions** — ADS-B data polled every 30 s, interpolated per-frame
- **14 distinct aircraft 3D models** mapped from ADS-B emitter category + ICAO type codes
- **Airspace overlays** — vector tiles showing controlled airspace boundaries
- **Flight trail history** — smoothed spline trails per aircraft
- **Airport information** — arrivals/departures, runway data, ATC frequencies, METAR/TAF
- **City-based airspace presets** with camera animation

---

## Key Observations for AI-ATC Extension

1. **ATC audio pipeline exists but is passive** — `use-atc-stream.ts` + `atc-panel.tsx` play audio only; no speech-to-text or analysis layer.
2. **Flight state is rich** — each `FlightState` carries callsign, ICAO hex, altitude, speed, heading, lat/lon, aircraft type; good substrate for conflict detection.
3. **Airspace data is available** — OpenAIP tiles give controlled airspace geometry; could be used for proximity/incursion alerts.
4. **No AI/LLM integration yet** — no Anthropic or OpenAI imports anywhere in the codebase.
5. **API routes are the natural extension point** — existing pattern of Next.js API routes proxying third-party services maps cleanly to adding an AI analysis endpoint.
6. **Test coverage is solid** — co-located `.test.ts` files throughout; TDD approach will fit naturally.
