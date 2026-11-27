"""SQLAlchemy database models."""

from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    String,
    ForeignKey,
    UniqueConstraint,
    func,
    text,
)

from src.backend.database import Base


class User(Base):
    """Application user associated with a hospital."""

    __tablename__ = "users"

    # Unique user ID.
    id = Column(
        Integer,
        primary_key=True,
        index=True,
    )

    # User's full name.
    full_name = Column(
        String(150),
        nullable=False,
    )

    # Hospital or healthcare organization.
    hospital_name = Column(
        String(255),
        nullable=False,
    )

    # Unique login email.
    email = Column(
        String(255),
        unique=True,
        index=True,
        nullable=False,
    )

    # Secure password hash, never the original password.
    hashed_password = Column(
        String(255),
        nullable=False,
    )

    # False = hospital user.
    # True = administrator.
    is_admin = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

    # Account activation status.
    is_active = Column(
        Boolean,
        nullable=False,
        default=True,
        server_default=text("true"),
    )

    # Account registration timestamp.
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # Records acceptance of platform terms.
    terms_accepted = Column(
        Boolean,
        nullable=False,
        default=False,
        server_default=text("false"),
    )

class AuditLog(Base):
    """Non-sensitive account and platform events for the admin activity feed."""

    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(50), nullable=False, index=True)
    description = Column(String(500), nullable=False)
    actor_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )


class WardContext(Base):
    """Database-backed static ward characteristics."""

    __tablename__ = "ward_contexts"

    id = Column(Integer, primary_key=True)
    city = Column(String(100), nullable=False, index=True)
    ward_id = Column(String(100), nullable=False, index=True)
    population_density_sqkm = Column(Float, nullable=True)
    water_logging_risk_index = Column(Float, nullable=True)
    sanitation_score = Column(Float, nullable=True)
    __table_args__ = (
        UniqueConstraint("city", "ward_id", name="uq_ward_context_city_ward"),
    )


class LiveObservation(Base):
    """A live weekly weather observation and its optional case-count provenance."""

    __tablename__ = "live_observations"

    id = Column(Integer, primary_key=True)
    city = Column(String(100), nullable=False, index=True)
    ward_id = Column(String(100), nullable=False, index=True)
    observation_date = Column(Date, nullable=False, index=True)
    avg_temp_c = Column(Float, nullable=False)
    max_temp_c = Column(Float, nullable=False)
    min_temp_c = Column(Float, nullable=False)
    rainfall_mm = Column(Float, nullable=False)
    humidity_pct = Column(Float, nullable=False)
    dengue_cases = Column(Float, nullable=False)
    reported_dengue_cases = Column(Float, nullable=True)
    dengue_case_source = Column(String(40), nullable=False, default="reported", server_default=text("'reported'"))
    submitted_by_user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )
    __table_args__ = (
        UniqueConstraint(
            "city",
            "ward_id",
            "observation_date",
            name="uq_live_observation_city_ward_date",
        ),
    )


class LivePrediction(Base):
    """Persisted live forecast trace."""

    __tablename__ = "live_predictions"

    id = Column(Integer, primary_key=True)
    observation_id = Column(
        Integer,
        ForeignKey("live_observations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    city = Column(String(100), nullable=False, index=True)
    ward_id = Column(String(100), nullable=False, index=True)
    observation_date = Column(Date, nullable=False, index=True)
    target_date = Column(Date, nullable=False)
    horizon_weeks = Column(Integer, nullable=False)
    predicted_cases = Column(Float, nullable=False)
    raw_predicted_cases = Column(Float, nullable=False)
    risk_category = Column(String(20), nullable=False)
    model_name = Column(String(100), nullable=False)
    experiment_id = Column(String(200), nullable=False)
    model_artifact = Column(String(300), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )


class HospitalCapacity(Base):
    """Operator-supplied hospital bed capacity. Unknown values remain null."""

    __tablename__ = "hospital_capacity"

    id = Column(Integer, primary_key=True)
    city = Column(String(100), nullable=False, index=True)
    hospital_name = Column(String(255), nullable=False)
    total_beds = Column(Integer, nullable=True)
    occupied_beds = Column(Integer, nullable=True)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    __table_args__ = (
        UniqueConstraint("city", "hospital_name", name="uq_hospital_city_name"),
    )


class WardBedCapacity(Base):
    """Local ward bed capacity and current occupancy for surge planning."""

    __tablename__ = "ward_bed_capacities"

    id = Column(Integer, primary_key=True)
    city = Column(String(100), nullable=False, index=True)
    ward_id = Column(String(100), nullable=False, index=True)
    total_beds = Column(Integer, nullable=False)
    occupied_beds = Column(Integer, nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    __table_args__ = (
        UniqueConstraint("city", "ward_id", name="uq_ward_bed_capacity_city_ward"),
    )


class CrewAvailability(Base):
    """Operator-supplied vector-control crew availability."""

    __tablename__ = "crew_availability"

    id = Column(Integer, primary_key=True)
    city = Column(String(100), nullable=False, index=True)
    crew_name = Column(String(255), nullable=False)
    available_units = Column(Integer, nullable=True)
    max_wards_per_week = Column(Integer, nullable=False, default=1)
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    __table_args__ = (
        UniqueConstraint("city", "crew_name", name="uq_crew_city_name"),
    )
