from datetime import date
from typing import Optional

from pydantic import BaseModel


class DailyWellness(BaseModel):
    date: date
    resting_heart_rate: Optional[int] = None   # bpm
    avg_stress: Optional[int] = None            # 0–100 (Garmin stress score)
    sleep_seconds: Optional[int] = None         # total sleep duration
    deep_sleep_seconds: Optional[int] = None
    rem_sleep_seconds: Optional[int] = None
    avg_cadence_spm: Optional[float] = None     # steps/min from running activity that day

    @property
    def sleep_hours(self) -> Optional[float]:
        return round(self.sleep_seconds / 3600, 1) if self.sleep_seconds else None

    @property
    def stress_label(self) -> Optional[str]:
        if self.avg_stress is None:
            return None
        if self.avg_stress < 26:
            return "low"
        if self.avg_stress < 51:
            return "medium"
        if self.avg_stress < 76:
            return "high"
        return "very_high"
