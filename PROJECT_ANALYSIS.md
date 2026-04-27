# Aeris ATC-AI — Front-End Visualization Analysis

## 1. Faking Aircraft Data for Simulations

### Architecture of the data pipeline

The data flows in one direction:

```
fetchFlightsByPoint()  →  useFlights()  →  FlightTracker  →  FlightLayers
(flight-api-client.ts)    (use-flights.ts)  (flight-tracker.tsx)  (flight-layers.tsx)
```

The cleanest interception point is **`useFlights`** — it returns `{ flights, loading, error, source }` and that interface is all `FlightTracker` needs. Everything else downstream is pure rendering.

### Recommended approach: dual-mode hook

**Step 1 — Create `src/hooks/use-simulated-flights.ts`** with the same return signature as `useFlights`. It generates `FlightState[]` locally on a `setInterval`, no network required:

```ts
// Generates N aircraft flying circular/approach/departure paths
export function useSimulatedFlights(city: City | null) {
  const [flights, setFlights] = useState<FlightState[]>([]);

  useEffect(() => {
    const id = setInterval(() => {
      setFlights(generateScenario(city, Date.now()));
    }, 1000); // 1-second update rate, not 5s
    return () => clearInterval(id);
  }, [city]);

  return { flights, loading: false, error: null, source: "simulation" };
}
```

Your `generateScenario()` function builds `FlightState` objects from parametric equations (circular holding patterns, ILS approaches, straight departures).

**Step 2 — In `src/components/flight-tracker.tsx` around line 46**, swap the hook based on a URL param:

```ts
const isSimMode = searchParams.get("mode") === "simulation";
const { flights } = isSimMode
  ? useSimulatedFlights(city)
  : useFlights(city, fpvIcao24, fpvSeedCenter);
```

**Step 3 (for backend-driven simulations)** — Add an SSE endpoint at `/api/simulation/stream` that your Python/Rust sim backend can push `FlightState[]` JSON to. The client hook listens with `EventSource`. This matches how you'd run many parallel scenarios — each simulation session connects to a different SSE stream identified by a session ID.

### The `FlightState` fields you need to populate per aircraft

```ts
{
  icao24: "AA1234",           // unique hex ID
  callsign: "UAL123",
  latitude: 33.9425,          // degrees WGS84
  longitude: -118.4081,
  baroAltitude: 3048,         // meters (10,000 ft = 3048m)
  geoAltitude: 3048,
  velocity: 257.2,            // m/s (500 kts ≈ 257 m/s)
  trueTrack: 270,             // degrees, 0=N, 90=E
  verticalRate: -5.08,        // m/s (-1000 fpm ≈ -5.08 m/s)
  onGround: false,
  typeCode: "B738",           // drives the 3D GLB model
  positionSource: 0,
}
```

The rendering engine already handles smooth interpolation between poll snapshots (the `smoothStep(smoothStep(smoothStep(t)))` easing loop in `flight-layers.tsx`), so at 1-second simulation updates it will look buttery smooth.

---

## 2. Flight Path Prediction Cones of Probability

### How to add them

deck.gl already has `PolygonLayer` and `SolidPolygonLayer` in the stack. The `buildTrailLayers` and `buildSelectionPulseLayers` in `src/components/map/flight-layer-builders.ts` show the exact pattern for adding new layers to the RAF loop.

**Step 1 — Cone geometry helper** (new file `src/lib/prediction-cone.ts`):

```ts
export function computePredictionCone(
  lat: number, lng: number,
  trackDeg: number,
  velocityMs: number,
  horizons: { seconds: number; lateralM: number }[],
): { contour: [number, number][] }[] {
  // For each horizon, project forward (lat, lng) at trackDeg for `seconds` of travel,
  // then sweep ±lateralM to form a fan polygon.
  // Returns one polygon per horizon ring (render outermost first).
}
```

The geographic projection math already exists in `src/components/map/camera-controller-utils.ts`. The forward projection formula is:

```ts
const distM = velocityMs * seconds;
const distDeg = distM / 111_320; // rough: 1° ≈ 111.32 km
const apexLat = lat + distDeg * Math.cos(trackRad);
const apexLng = lng + distDeg * Math.sin(trackRad) / Math.cos(lat * DEG2RAD);
```

**Step 2 — New builder function** in `src/components/map/flight-layer-builders.ts`:

```ts
import { PolygonLayer } from "@deck.gl/layers";

export function buildPredictionConeLayers(
  flights: FlightState[],
  selectedIcao24: string | null,
) {
  // Either show cones for all flights, or just the selected one
  const targets = selectedIcao24
    ? flights.filter(f => f.icao24 === selectedIcao24)
    : flights;

  const coneData = targets.flatMap(f => computePredictionCone(...));

  return [
    new PolygonLayer({
      id: "prediction-cones",
      data: coneData,
      getPolygon: d => d.contour,
      getFillColor: d => [255, 200, 0, d.alpha],  // amber gradient by horizon
      getLineColor: [255, 200, 0, 80],
      filled: true,
      stroked: true,
      lineWidthMinPixels: 1,
      pickable: false,
    }),
  ];
}
```

**Step 3 — Wire it into the RAF loop** in `flight-layers.tsx` around line 696, alongside the existing `buildTrailLayers` call:

```ts
const coneLayers = buildPredictionConeLayers(
  interpolatedFlights,
  selectedIcao24,
);
overlay.setProps({ layers: [...coneLayers, ...trailLayers, ...aircraftLayers] });
```

### Probability ring design

| Ring | Time horizon | Lateral half-width | Opacity |
|------|-------------|-------------------|---------|
| Inner | 30s | ~0.5 NM | 60% |
| Mid | 90s | ~2 NM | 35% |
| Outer | 3 min | ~5 NM | 15% |
| Far | 5 min | ~10 NM | 8% |

For AI-provided uncertainty: your simulation backend can push a `predictionConeParams` alongside each `FlightState` with per-aircraft uncertainty bounds instead of defaults.

### 3D altitude cones

`SolidPolygonLayer` with `extruded: true` and `getElevation` gives vertical extent — the cone's height range represents altitude uncertainty (±1000 ft, etc.). This requires altitude-aware lat/lng projection since MapLibre's 3D terrain is in play, but `altitudeToElevation()` in `src/lib/flight-utils.ts` already handles the coordinate transform.

---

## 3. Camera Control and Automation

### What already exists (it's extensive)

| System | File | What it does |
|--------|------|-------------|
| Auto-orbit | `use-orbit-camera.ts` | Rotates bearing 0.06°/frame after 5s idle |
| FPV follow | `use-fpv-camera.ts` | Locks onto one aircraft with EMA-smoothed pan/zoom/bearing |
| Follow mode | `camera-controller.tsx:75–142` | Flies to aircraft then eases continuously |
| City flyTo | `camera-controller.tsx:59–72` | `zoom=9.2, pitch=49, bearing=27.4`, 2800ms |
| Keyboard | `use-keyboard-camera.ts` | Velocity-based with acceleration/deceleration via DOM events `aeris:camera-start/stop` |
| North-up | `camera-controller.tsx:163–193` | Smoothstep RAF animation to bearing=0 |

All camera settings are also user-configurable via `use-settings.tsx` (orbit speed, direction, FPV chase distance, globe mode).

### What to add for ATC simulation

**A. Programmatic camera control via DOM events** (extends the existing keyboard event pattern):

The current system already listens to `aeris:camera-start` / `aeris:camera-stop` custom DOM events. Add:

```ts
// Dispatch from your sim control panel:
window.dispatchEvent(new CustomEvent("aeris:camera-fly-to", {
  detail: { lat: 33.94, lng: -118.40, zoom: 11, pitch: 60, bearing: 280, duration: 3000 }
}));
window.dispatchEvent(new CustomEvent("aeris:camera-follow", {
  detail: { icao24: "a1b2c3" }  // triggers FPV mode on that aircraft
}));
```

**B. Fit-to-bounds for multi-aircraft overview**: deck.gl's `WebMercatorViewport.fitBounds()` can compute the zoom/center to show all simulated aircraft. Useful for "overview" mode during scenario setup.

**C. Cinematic sequence scripting**: Chain `map.flyTo()` calls with Promise-based sequencing — fly to runway threshold, pause, pull back to overview, sweep to conflict zone. The `flyTo`/`easeTo` APIs already accept `eventData` so you can detect when each leg completes via the `moveend` event.

**D. Simulation timeline scrubbing**: Add a `useSimulationTime(timestamp)` hook that replays recorded `FlightState[]` snapshots. The camera can advance with it. The interpolation system in `flight-layers.tsx` (the snapshot mechanism at lines 203–314) would naturally handle smooth playback between recorded frames.

### Immediately useful settings to control programmatically

You can set any of these via the settings context:

```ts
const { updateSettings } = useSettings();
// For simulation overview:
updateSettings({ autoOrbit: false, showTrails: true, trailDistance: 200, showAltitudeColors: true });
// For cinematic follow:
updateSettings({ autoOrbit: false });  // then trigger FPV on a specific ICAO
```

Full settings schema (persisted to `localStorage` under `aeris:settings`):

| Setting | Type | Default |
|---------|------|---------|
| `autoOrbit` | `boolean` | `true` |
| `orbitSpeed` | `number` | `0.06` |
| `orbitDirection` | `"clockwise" \| "counter-clockwise"` | `"clockwise"` |
| `showTrails` | `boolean` | `true` |
| `trailThickness` | `number` | `1.3` |
| `trailDistance` | `number` | `80` (NM) |
| `showShadows` | `boolean` | `true` |
| `showAltitudeColors` | `boolean` | `true` |
| `altitudeDisplayMode` | `"presentation" \| "realistic"` | `"presentation"` |
| `unitSystem` | `"aviation" \| "metric" \| "imperial"` | `"aviation"` |
| `fpvChaseDistance` | `number` | `0.0048` |
| `globeMode` | `boolean` | `false` |
| `showAirspace` | `boolean` | `false` |
| `airspaceOpacity` | `number` | `0.78` |
| `showWeatherRadar` | `boolean` | `false` |
| `weatherRadarOpacity` | `number` | `0.5` |

---

## Summary

| Goal | Recommended approach | Key files |
|------|---------------------|-----------|
| Fake data (local) | `useSimulatedFlights` hook, swap in `flight-tracker.tsx` | `src/hooks/use-flights.ts` (interface to match) |
| Fake data (backend-driven) | SSE endpoint + `EventSource` hook | `src/app/api/simulation/stream.ts` (new) |
| Prediction cones | `PolygonLayer` in `flight-layer-builders.ts` + geometry helper | `src/lib/prediction-cone.ts` (new) + `flight-layers.tsx:696` |
| Camera automation | Extend DOM event system, add `aeris:camera-fly-to` | `use-keyboard-camera.ts` (pattern to follow) |

### Key architectural gap

The app is purely pull-based (5-second HTTP polling). For simulation you'll want to flip that: push state from your backend on the simulation's own timestep. An SSE endpoint at `/api/simulation/stream` is the lowest-friction way to add that without touching the existing data pipeline. Each parallel simulation session connects to a different stream identified by a session ID in the query string.
