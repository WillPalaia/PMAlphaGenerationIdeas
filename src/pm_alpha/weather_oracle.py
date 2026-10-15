"""External weather observation oracle client for prediction market verification.

Connects to the National Weather Service (NWS / NOAA) public API to obtain
official ground-truth airport station observations (ASOS / Synoptic Data network).
Provides margin-of-safety filtering and nowcast validation for weather contracts.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Mapping from Kalshi ticker prefixes to primary NOAA / NWS ASOS station identifiers
TICKER_STATION_MAP: Dict[str, str] = {
    "KXTEMPLAXHS": "KLAX",
    "KXTEMPLAXH": "KLAX",
    "KXTEMPLAX": "KLAX",
    "KXTEMPCHIHS": "KORD",
    "KXTEMPCHIMETROHS": "KORD",
    "KXTEMPCHI": "KORD",
    "KXTEMPNYCHS": "KNYC",
    "KXTEMPNYCH": "KNYC",
    "KXTEMPNYC": "KNYC",
    "KXTEMPMIAH": "KMIA",
    "KXTEMPMIA": "KMIA",
    "KXTEMPAUSH": "KAUS",
    "KXTEMPBOSH": "KBOS",
    "KXHIGHDEN": "KDEN",
    "KXDENSNOW": "KDEN",
    "KXLOWTPHX": "KPHX",
    "KXAVGTKPHX": "KPHX",
    "KXLOWTHOU": "KHOU",
    "KXHOBBYTEMP": "KHOU",
    "KXRAINSEA": "KSEA",
    "KXHIGHPHIL": "KPHL",
    "KXLOWTPHIL": "KPHL",
    "KXHIGHTDC": "KDCA",
    "KXHIGHTATL": "KATL",
    "KXLOWTATL": "KATL",
}


@dataclass(frozen=True)
class NwsObservation:
    station_id: str
    timestamp_iso: str
    temp_f: float
    temp_c: float
    dewpoint_f: Optional[float]
    wind_speed_mph: Optional[float]
    description: str
    fetched_at_ms: int


@dataclass(frozen=True)
class OracleVerdict:
    station_id: Optional[str]
    strike_temp_f: Optional[float]
    current_temp_f: Optional[float]
    margin_f: Optional[float]
    verdict: str  # "CONFIRMED_FAVORITE", "MARGIN_TOO_CLOSE", "IMPOSSIBLE_TARGET", "NO_DATA"
    reason: str


def parse_strike_temperature(ticker: str) -> Optional[float]:
    """Extract strike temperature from Kalshi ticker like KXTEMPLAXHS-26OCT1502-T71.99."""
    match = re.search(r"-T([0-9]+(?:\.[0-9]+)?)", ticker)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            return None
    return None


def resolve_station(ticker: str) -> Optional[str]:
    """Resolve the NWS airport weather station ID from a Kalshi ticker."""
    prefix = ticker.upper().split("-")[0]
    return TICKER_STATION_MAP.get(prefix)


class WeatherOracle:
    """Client for fetching live NWS ASOS observations with in-memory caching."""

    def __init__(self, cache_ttl_seconds: float = 120.0, timeout: float = 8.0) -> None:
        self.cache_ttl = cache_ttl_seconds
        self.timeout = timeout
        self._cache: Dict[str, Tuple[float, NwsObservation]] = {}

    def get_latest_observation(self, station_id: str) -> Optional[NwsObservation]:
        """Fetch latest ASOS observation for station with caching."""
        st = station_id.upper()
        now = time.time()

        if st in self._cache:
            cache_time, obs = self._cache[st]
            if now - cache_time < self.cache_ttl:
                return obs

        url = f"https://api.weather.gov/stations/{st}/observations/latest"
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "PMAlphaResearch (research@pmalpha.local)",
                "Accept": "application/geo+json",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.load(resp)
            props = data.get("properties", {})
            temp_c = props.get("temperature", {}).get("value")
            if temp_c is None:
                return None

            temp_f = temp_c * 9.0 / 5.0 + 32.0
            dew_c = props.get("dewpoint", {}).get("value")
            dew_f = (dew_c * 9.0 / 5.0 + 32.0) if dew_c is not None else None

            wind_kmh = props.get("windSpeed", {}).get("value")
            wind_mph = (wind_kmh * 0.621371) if wind_kmh is not None else None

            obs = NwsObservation(
                station_id=st,
                timestamp_iso=props.get("timestamp", ""),
                temp_f=round(temp_f, 2),
                temp_c=round(temp_c, 2),
                dewpoint_f=round(dew_f, 2) if dew_f is not None else None,
                wind_speed_mph=round(wind_mph, 1) if wind_mph is not None else None,
                description=props.get("textDescription", "Unknown"),
                fetched_at_ms=int(now * 1000),
            )
            self._cache[st] = (now, obs)
            return obs
        except Exception as exc:
            logger.debug("Failed to fetch NWS observation for %s: %s", st, exc)
            return None

    def evaluate(self, ticker: str, side: str = "yes") -> OracleVerdict:
        """Evaluate margin of safety for a Kalshi weather temperature contract."""
        station = resolve_station(ticker)
        strike = parse_strike_temperature(ticker)

        if not station or strike is None:
            return OracleVerdict(
                station_id=station,
                strike_temp_f=strike,
                current_temp_f=None,
                margin_f=None,
                verdict="NO_DATA",
                reason="Unmapped weather station or unparseable strike temperature",
            )

        obs = self.get_latest_observation(station)
        if not obs:
            return OracleVerdict(
                station_id=station,
                strike_temp_f=strike,
                current_temp_f=None,
                margin_f=None,
                verdict="NO_DATA",
                reason=f"Station {station} observation unavailable",
            )

        margin = obs.temp_f - strike

        # For YES bets expecting temp > strike:
        if side.lower() in ("yes", "buy"):
            if margin >= 2.0:
                return OracleVerdict(
                    station_id=station,
                    strike_temp_f=strike,
                    current_temp_f=obs.temp_f,
                    margin_f=round(margin, 2),
                    verdict="CONFIRMED_FAVORITE",
                    reason=f"Current temp ({obs.temp_f}°F) is +{margin:.2f}°F above strike ({strike}°F)",
                )
            elif -1.0 <= margin < 1.5:
                return OracleVerdict(
                    station_id=station,
                    strike_temp_f=strike,
                    current_temp_f=obs.temp_f,
                    margin_f=round(margin, 2),
                    verdict="MARGIN_TOO_CLOSE",
                    reason=f"Current temp ({obs.temp_f}°F) is within high-variance buffer of strike ({strike}°F)",
                )
            elif margin < -2.5:
                return OracleVerdict(
                    station_id=station,
                    strike_temp_f=strike,
                    current_temp_f=obs.temp_f,
                    margin_f=round(margin, 2),
                    verdict="IMPOSSIBLE_TARGET",
                    reason=f"Current temp ({obs.temp_f}°F) is substantially below strike ({strike}°F)",
                )
            else:
                return OracleVerdict(
                    station_id=station,
                    strike_temp_f=strike,
                    current_temp_f=obs.temp_f,
                    margin_f=round(margin, 2),
                    verdict="NEUTRAL",
                    reason=f"Margin is {margin:.2f}°F",
                )

        return OracleVerdict(
            station_id=station,
            strike_temp_f=strike,
            current_temp_f=obs.temp_f,
            margin_f=round(margin, 2),
            verdict="NEUTRAL",
            reason="Unchecked side",
        )
