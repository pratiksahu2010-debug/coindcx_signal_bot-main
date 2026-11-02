from datetime import datetime
from sqlalchemy import String, Integer, Text, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from database.db import Base

class StepChannelStateRecord(Base):
    __tablename__ = "step_channel_state"
    pair: Mapped[str] = mapped_column(String(32), primary_key=True)
    direction: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    event: Mapped[str] = mapped_column(String(16), default="")
    event_time_ms: Mapped[int] = mapped_column(Integer, default=0)
    candle_time_ms: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
