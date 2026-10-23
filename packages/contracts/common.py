from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, RootModel, model_validator


def valid_id(value: str) -> str:
    UUID(value)
    return value


def valid_time(value: str) -> str:
    if not value.endswith("Z") or "T" not in value:
        raise ValueError("UTC time must end with Z")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError("UTC timezone required")
    return value


ID = Annotated[str, AfterValidator(valid_id)]
Time = Annotated[str, AfterValidator(valid_time)]
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Text = Annotated[str, Field(max_length=2000)]
Reasons = Annotated[list[Text], Field(max_length=10)]


def uid() -> str:
    return str(uuid4())


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Envelope(Model):
    schema_version: Literal["1.0"]


class SourceKind(StrEnum):
    EXAMPLE = "EXAMPLE"
    SIMULATION = "SIMULATION"
    REAL = "REAL"


class PID(RootModel[dict[str, Number]]):
    """A named numeric parameter set. kp/ki/kd remain the example's parameters."""

    def __init__(self, **data: float):
        super().__init__(root=data)

    @model_validator(mode="after")
    def valid_parameters(self):
        if not 1 <= len(self.root) <= 32 or any(
            not key.isidentifier() or len(key) > 64 for key in self.root
        ):
            raise ValueError("1..32 named parameters are required")
        return self

    def __getattr__(self, key: str):
        try:
            return super().__getattr__(key)
        except AttributeError:
            try:
                return self.root[key]
            except KeyError:
                raise AttributeError(key) from None


# New integrations may use the descriptive name; legacy API contracts retain PID.
ParameterSet = PID


class ErrorDetail(Model):
    code: str
    message: Text
    retryable: bool
    trace_id: ID


class ComponentHealth(Model):
    name: str
    ready: bool
    version: str | None
    error_code: str | None


class Bound(Model):
    min: Number
    max: Number
    max_delta: Annotated[Number, Field(ge=0)]

    @model_validator(mode="after")
    def valid_interval(self):
        if self.min > self.max:
            raise ValueError("Parameter minimum exceeds maximum")
        return self


class Constraints(Model):
    kp: Bound | None = None
    ki: Bound | None = None
    kd: Bound | None = None
    parameter_bounds: dict[str, Bound] | None = None
    equality_tolerance: Annotated[Number, Field(ge=0)]

    @model_validator(mode="after")
    def bounds_present(self):
        if self.parameter_bounds is not None:
            if not 1 <= len(self.parameter_bounds) <= 32:
                raise ValueError("1..32 parameter bounds are required")
            if any(key in {"kp", "ki", "kd"} and getattr(self, key) is not None
                   for key in self.parameter_bounds):
                raise ValueError("Duplicate parameter bounds")
        elif any(getattr(self, key) is None for key in ("kp", "ki", "kd")):
            raise ValueError("Legacy PID requires kp, ki and kd bounds")
        return self

    def bounds(self) -> dict[str, Bound]:
        result = {key: getattr(self, key) for key in ("kp", "ki", "kd")
                  if getattr(self, key) is not None}
        result.update(self.parameter_bounds or {})
        return result


class MetricsConfig(Model):
    metrics_version: Literal["metrics-v1"]
    steady_tail_fraction: Number
    settling_band_absolute: Number
    settling_hold_seconds: Number
    hard_pressure_min: Number
    hard_pressure_max: Number
    target_error: Number
    target_overshoot: Number
    target_std: Number
    target_settling: Number
    min_improvement: Number
