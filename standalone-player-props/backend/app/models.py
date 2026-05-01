from pydantic import BaseModel
from typing import List, Optional


class Player(BaseModel):
    player_id: str
    name: str
    team: str
    league: str
    team_id: Optional[str] = None
    projected_minutes: float
    avg_minutes: Optional[float] = None
    usage_score: float
    usage_rate: Optional[float] = None
    last_10_games_minutes: Optional[List[float]] = None
    prop_frequency: float
    top50_score: float
    role: Optional[str] = None
    is_superstar: Optional[bool] = None
    consensus_rank: Optional[int] = None
    consensus_tier: Optional[str] = None
    is_available: Optional[bool] = None
    next_game: Optional[dict] = None


class PropProjection(BaseModel):
    player_id: str
    player_name: str
    team: str
    league: str
    prop_type: str
    line: Optional[float] = None
    odds_over: float
    odds_under: float
    projection: float
    over_probability: float
    under_probability: float
    ev_over_percent: float
    ev_under_percent: float
    confidence_score: float
    picked_side: str
    model_confidence: Optional[dict] = None
    model_agreement: Optional[float] = None
    model_variance: Optional[float] = None


class PropsResponse(BaseModel):
    league: str
    count: int
    items: List[PropProjection]
    excluded_players: Optional[List[dict]] = None
    model_variance: Optional[List[dict]] = None
    sanity_flags: Optional[List[dict]] = None


class PlayersResponse(BaseModel):
    league: str
    count: int
    items: List[Player]
    excluded_players: Optional[List[dict]] = None


class ProjectionFilters(BaseModel):
    league: str
    prop_type: Optional[str] = None
    side: Optional[str] = None
    min_ev: Optional[float] = None
