#!/usr/bin/env python3
"""
Aeris ATC Simulation Server
============================
Serves fake flight data in readsb JSON format so the Aeris front end
can be driven without a live ADS-B feed.

Usage:
    python sim_server.py [--port PORT]

Then open the app with ?provider=simulation in the URL:
    http://localhost:3000/?provider=simulation

Endpoint served:
    GET /v2/point/{lat}/{lon}/{radius_nm}

The server generates a small fleet of aircraft centred on whatever
lat/lon the front end queries, so the planes always appear in view
regardless of which city is selected.
"""

import json
import math
import re
import time
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer

DEFAULT_PORT = 8888

# ---------------------------------------------------------------------------
# Fleet definition
# Each row: (icao24, callsign, registration, type_code,
#            base_alt_ft, base_speed_kts, pattern, initial_bearing_deg)
#
# type_code drives the 3-D model (see aircraft-model-mapping.ts):
#   B738  → Boeing 737     B77W → Boeing 777   A320 → Airbus A320
#   A388  → Airbus A380    CRJ9 → Regional jet  C172 → Light prop
#   B06   → Helicopter     E55P → Bizjet
# ---------------------------------------------------------------------------
FLEET = [
    ("a1b2c3", "UAL123  ", "N12345", "B738", 35_000, 450, "straight",  45),
    ("b2c3d4", "DAL456  ", "N67890", "A320", 28_000, 420, "straight", 135),
    ("c3d4e5", "AAL789  ", "N11111", "B77W", 38_000, 480, "straight", 225),
    ("d4e5f6", "SWA101  ", "N22222", "B737", 31_000, 430, "straight", 315),
    ("e5f6a7", "JBU202  ", "N33333", "A321",  8_000, 250, "holding",    0),
    ("f6a7b8", "SKW303  ", "N44444", "CRJ9",  3_000, 190, "approach",  60),
    ("a7b8c9", "N456AB  ", "N55555", "C172",  2_500,  90, "vfr",       0),
    ("b8c9d0", "FDX404  ", "N66666", "B752", 32_000, 440, "straight",  90),
    ("c9d0e1", "NKS505  ", "N77777", "A321", 25_000, 410, "straight", 160),
    ("d0e1f2", "HAL606  ", "N88888", "A388", 40_000, 490, "straight", 280),
    ("e1f2a3", "N789HX  ", "N99999", "E55P", 41_000, 380, "straight", 200),
    ("f2a3b4", "PHX777  ", "N10101", "B06",   1_200,  80, "helicopter", 0),
]


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _deg2rad(d: float) -> float:
    return d * math.pi / 180.0


def _rad2deg(r: float) -> float:
    return r * 180.0 / math.pi


def project(lat: float, lng: float, bearing_deg: float, dist_nm: float):
    """Return (lat, lng) after travelling dist_nm NM on bearing_deg."""
    d = dist_nm / 3440.065          # NM → radians of arc
    lat1 = _deg2rad(lat)
    lng1 = _deg2rad(lng)
    b = _deg2rad(bearing_deg)
    lat2 = math.asin(
        math.sin(lat1) * math.cos(d) +
        math.cos(lat1) * math.sin(d) * math.cos(b)
    )
    lng2 = lng1 + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(lat1),
        math.cos(d) - math.sin(lat1) * math.sin(lat2)
    )
    return _rad2deg(lat2), _rad2deg(lng2)


# ---------------------------------------------------------------------------
# Motion patterns
# ---------------------------------------------------------------------------

def _aircraft_state(row, center_lat: float, center_lng: float, t: float):
    """
    Return (lat, lng, track_deg, alt_ft, vrate_fpm, speed_kts) for one
    aircraft at epoch time t.
    """
    icao, callsign, reg, type_code, base_alt, base_spd, pattern, bearing = row
    # Spread aircraft across the cycle using a hash of the ICAO so each
    # plane starts at a different point in its pattern.
    phase_frac = (int(icao, 16) % 1000) / 1000.0

    if pattern == "straight":
        cycle_nm = 300.0
        start_nm = phase_frac * cycle_nm - cycle_nm / 2.0
        dist_nm = (t * base_spd / 3600.0 + start_nm) % cycle_nm - cycle_nm / 2.0
        lat, lng = project(center_lat, center_lng, bearing, dist_nm)
        return lat, lng, bearing % 360, base_alt, 0, base_spd

    elif pattern == "holding":
        radius_nm = 5.0
        period_s = 240.0
        angle_deg = (phase_frac * 360.0 + t / period_s * 360.0) % 360.0
        offset_lat = 0.15
        offset_lng = -0.15
        lat, lng = project(
            center_lat + offset_lat,
            center_lng + offset_lng,
            angle_deg, radius_nm
        )
        track = (angle_deg + 90.0) % 360.0
        return lat, lng, track, base_alt, 0, base_spd

    elif pattern == "approach":
        # Fly inbound from bearing direction, descend to pattern altitude,
        # then reset to 20 NM out.
        cycle_s = 420.0
        frac = (phase_frac + t / cycle_s) % 1.0
        dist_nm = 20.0 * (1.0 - frac)
        alt = max(500, int(base_alt * (1.0 - frac)))
        vrate = -800 if alt > 800 else 0
        spd = max(140, int(base_spd * (1.0 - frac * 0.25)))
        lat, lng = project(center_lat, center_lng, bearing, dist_nm)
        track = (bearing + 180.0) % 360.0   # flying toward center
        return lat, lng, track, alt, vrate, spd

    elif pattern == "vfr":
        radius_nm = 3.0
        period_s = 200.0
        angle_deg = (phase_frac * 360.0 + t / period_s * 360.0) % 360.0
        lat, lng = project(
            center_lat - 0.08,
            center_lng + 0.08,
            angle_deg, radius_nm
        )
        track = (angle_deg + 90.0) % 360.0
        return lat, lng, track, base_alt, 0, base_spd

    elif pattern == "helicopter":
        # Slow orbit very close to the center
        radius_nm = 1.0
        period_s = 120.0
        angle_deg = (phase_frac * 360.0 + t / period_s * 360.0) % 360.0
        lat, lng = project(center_lat, center_lng, angle_deg, radius_nm)
        track = (angle_deg + 90.0) % 360.0
        return lat, lng, track, base_alt, 0, base_spd

    # Fallback: stationary
    return center_lat, center_lng, 0.0, base_alt, 0, base_spd


# ---------------------------------------------------------------------------
# Response builder
# ---------------------------------------------------------------------------

# DO-260B category codes used by the front end for model selection fallback.
_CATEGORY_MAP = {
    "B738": "A3", "B737": "A3", "A320": "A3", "A321": "A3",
    "B77W": "A5", "B752": "A4", "A388": "A5",
    "CRJ9": "A2", "E55P": "A2",
    "C172": "A1",
    "B06":  "B1",   # helicopter
}


def build_response(center_lat: float, center_lng: float, _radius_nm: float) -> dict:
    t = time.time()
    aircraft = []

    for row in FLEET:
        lat, lng, track, alt_ft, vrate, spd = _aircraft_state(
            row, center_lat, center_lng, t
        )
        icao, callsign, reg, type_code, *_ = row
        category = _CATEGORY_MAP.get(type_code, "A3")

        aircraft.append({
            "hex":      icao,
            "type":     "adsb_icao",
            "flight":   callsign,
            "r":        reg,
            "t":        type_code,
            "lat":      round(lat, 6),
            "lon":      round(lng, 6),
            "alt_baro": int(alt_ft),
            "alt_geom": int(alt_ft) + 50,
            "gs":       float(round(spd, 1)),
            "track":    float(round(track % 360, 1)),
            "baro_rate": float(vrate),
            "category": category,
            "squawk":   "1200",
            "emergency": "none",
            "seen_pos": 0.3,
            "seen":     0.3,
            "messages": 1000,
            "rssi":     -18.5,
            "mlat":     [],
            "tisb":     [],
        })

    return {
        "ac":    aircraft,
        "msg":   "No error",
        "now":   t,
        "total": len(aircraft),
        "ctime": int(t * 1000),
        "ptime": 1,
    }


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

# Matches /v2/point/{lat}/{lon}/{radius}
_POINT_RE = re.compile(
    r"^/v2/point/(-?\d+(?:\.\d+)?)/(-?\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)"
)


class SimHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        m = _POINT_RE.match(self.path)
        if not m:
            self.send_error(404, "Only /v2/point/{lat}/{lon}/{radius} is supported")
            return

        clat = float(m.group(1))
        clng = float(m.group(2))
        radius_nm = float(m.group(3))

        payload = json.dumps(build_response(clat, clng, radius_nm)).encode()

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt, *args):
        ts = time.strftime("%H:%M:%S")
        print(f"[sim {ts}] {self.address_string()} — {fmt % args}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Aeris ATC simulation server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"Port to listen on (default: {DEFAULT_PORT})")
    args = parser.parse_args()

    server = HTTPServer(("", args.port), SimHandler)
    print(f"Aeris simulation server listening on port {args.port}")
    print(f"Open the app with: http://localhost:3000/?provider=simulation")
    print("Press Ctrl-C to stop.\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
