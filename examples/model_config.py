HORIZON_MODEL_CONFIG = {
    15: {"loss": "absolute_error", "half_life_days": 60.0},
    30: {"loss": "squared_error", "half_life_days": 60.0},
    45: {"loss": "poisson", "half_life_days": 30.0},
    60: {"loss": "poisson", "half_life_days": 60.0},
}