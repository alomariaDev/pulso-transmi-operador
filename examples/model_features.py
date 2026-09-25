from __future__ import annotations

import pandas as pd


def build_features(observations: pd.DataFrame, context: pd.DataFrame) -> pd.DataFrame:
    frame = observations.copy()
    frame["observed_at"] = pd.to_datetime(frame["observed_at"], utc=True)
    frame["station_id"] = frame["station_id"].astype("string")
    frame = frame.sort_values(["station_id", "observed_at"]).reset_index(drop=True)
    if frame.duplicated(["station_id", "observed_at"]).any():
        raise ValueError("Hay observaciones duplicadas por estación y timestamp")
    for station_id, station_rows in frame.groupby("station_id", sort=False):
        gaps = station_rows["observed_at"].diff().dropna()
        if not gaps.eq(pd.Timedelta(minutes=15)).all():
            first_gap = gaps.loc[gaps.ne(pd.Timedelta(minutes=15))].index[0]
            timestamp = frame.loc[first_gap, "observed_at"].isoformat()
            raise ValueError(
                f"La serie de {station_id} tiene un hueco o intervalo inválido cerca de {timestamp}"
            )
    context = context.copy()
    context["observed_at"] = pd.to_datetime(context["observed_at"], utc=True)
    frame = frame.merge(context, on="observed_at", how="left", validate="many_to_one")
    grouped_demand = frame.groupby("station_id")["demand"]
    for periods, name in ((1, "lag_15m"), (4, "lag_1h"), (96, "lag_1d"), (672, "lag_7d")):
        frame[name] = grouped_demand.shift(periods)
    shifted = grouped_demand.shift(1)
    frame["rolling_mean_1h"] = shifted.groupby(frame["station_id"]).transform(
        lambda values: values.rolling(4, min_periods=4).mean()
    )
    frame["rolling_mean_1d"] = shifted.groupby(frame["station_id"]).transform(
        lambda values: values.rolling(96, min_periods=96).mean()
    )
    frame["rolling_std_1d"] = shifted.groupby(frame["station_id"]).transform(
        lambda values: values.rolling(96, min_periods=96).std()
    )
    frame["hour"] = frame["observed_at"].dt.hour
    frame["quarter_hour"] = frame["observed_at"].dt.minute // 15
    frame["weekday"] = frame["observed_at"].dt.dayofweek
    frame["is_weekend"] = (frame["weekday"] >= 5).astype(int)
    station_codes = {
        station_id: code
        for code, station_id in enumerate(sorted(frame["station_id"].dropna().unique()))
    }
    frame["station_code"] = frame["station_id"].map(station_codes)
    return frame
