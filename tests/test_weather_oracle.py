from __future__ import annotations

import unittest
from unittest.mock import patch, MagicMock

from pm_alpha.weather_oracle import (
    WeatherOracle,
    NwsObservation,
    OracleVerdict,
    parse_strike_temperature,
    resolve_station,
)


def test_parse_strike_temperature():
    assert parse_strike_temperature("KXTEMPLAXHS-26OCT1502-T71.99") == 71.99
    assert parse_strike_temperature("KXTEMPCHIHS-26OCT0816-T70.99") == 70.99
    assert parse_strike_temperature("KXTEMPMIAH-26OCT1321-T80.99") == 80.99
    assert parse_strike_temperature("KXRAIN-26OCT09-SEA") is None


def test_resolve_station():
    assert resolve_station("KXTEMPLAXHS-26OCT1502-T71.99") == "KLAX"
    assert resolve_station("KXTEMPCHIHS-26OCT0816-T70.99") == "KORD"
    assert resolve_station("KXTEMPNYCHS-26OCT0814-T73.99") == "KNYC"
    assert resolve_station("KXTEMPMIAH-26OCT1321-T80.99") == "KMIA"
    assert resolve_station("KXTEMPAUSH-26OCT15-T85.00") == "KAUS"
    assert resolve_station("KXUNKNOWN-26") is None


def test_weather_oracle_evaluate_confirmed_favorite():
    oracle = WeatherOracle(cache_ttl_seconds=60)
    # Mock observation: station is 75.0°F, strike is 71.99°F -> margin = +3.01°F
    mock_obs = NwsObservation(
        station_id="KLAX",
        timestamp_iso="2026-10-15T05:00:00Z",
        temp_f=75.0,
        temp_c=23.89,
        dewpoint_f=60.0,
        wind_speed_mph=5.0,
        description="Clear",
        fetched_at_ms=1700000000000,
    )
    with patch.object(oracle, "get_latest_observation", return_value=mock_obs):
        res = oracle.evaluate("KXTEMPLAXHS-26OCT1502-T71.99", side="yes")
        assert res.verdict == "CONFIRMED_FAVORITE"
        assert res.margin_f == 3.01
        assert res.current_temp_f == 75.0


def test_weather_oracle_evaluate_margin_too_close():
    oracle = WeatherOracle(cache_ttl_seconds=60)
    # Mock observation: station is 74.2°F, strike is 73.99°F -> margin = +0.21°F (Knife-edge zone!)
    mock_obs = NwsObservation(
        station_id="KNYC",
        timestamp_iso="2026-10-15T05:00:00Z",
        temp_f=74.2,
        temp_c=23.44,
        dewpoint_f=55.0,
        wind_speed_mph=3.0,
        description="Partly Cloudy",
        fetched_at_ms=1700000000000,
    )
    with patch.object(oracle, "get_latest_observation", return_value=mock_obs):
        res = oracle.evaluate("KXTEMPNYCHS-26OCT0814-T73.99", side="yes")
        assert res.verdict == "MARGIN_TOO_CLOSE"
        assert res.margin_f == 0.21


def test_weather_oracle_evaluate_impossible_target():
    oracle = WeatherOracle(cache_ttl_seconds=60)
    # Mock observation: station is 66.0°F, strike is 71.99°F -> margin = -5.99°F (impossible)
    mock_obs = NwsObservation(
        station_id="KLAX",
        timestamp_iso="2026-10-15T05:00:00Z",
        temp_f=66.0,
        temp_c=18.89,
        dewpoint_f=58.0,
        wind_speed_mph=8.0,
        description="Clear",
        fetched_at_ms=1700000000000,
    )
    with patch.object(oracle, "get_latest_observation", return_value=mock_obs):
        res = oracle.evaluate("KXTEMPLAXHS-26OCT1502-T71.99", side="yes")
        assert res.verdict == "IMPOSSIBLE_TARGET"
        assert res.margin_f == -5.99
