HORIZON_MODEL_CONFIG = {
    15: {"loss": "squared_error", "half_life_days": 60.0},
    30: {"loss": "squared_error", "half_life_days": 60.0},
    45: {"loss": "squared_error", "half_life_days": 30.0},
    60: {"loss": "squared_error", "half_life_days": 60.0},
}
TARGET_TRANSFORM = {
    "mode": "relative_to_lag_15m",
    "denominator_feature": "lag_15m",
    "denominator_floor": 10.0,
}