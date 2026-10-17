"""Governed LiftHaul public fare-matrix registry.

The registry deliberately separates a benchmark-informed candidate used for transparent estimates
from a commercially approved matrix that may support collection and provider settlement. Merely
committing rates, setting a version, or passing a test can never fabricate Commercial, Finance or
Tax approval.
"""
from __future__ import annotations

import copy
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path


REGISTRY_PATH = Path(__file__).resolve().parents[1] / "data" / "fare_matrix_registry.json"
APPROVED = "APPROVED"
INDICATIVE = "INDICATIVE_ESTIMATE_ONLY"
REQUIRED_APPROVAL_FIELDS = (
    "commercial_owner",
    "finance_owner",
    "tax_owner",
    "decision_date",
    "effective_at",
    "provider_share_basis",
    "tax_treatment_reference",
    "approval_artifact",
)
ALLOWED_STATUSES = {"DRAFT", "AWAITING_APPROVAL", "APPROVED", "RETIRED"}


class FareMatrixError(ValueError):
    """Raised when registry data or an activation decision violates governance."""


def _decimal(value, field):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FareMatrixError(f"{field} must be numeric") from exc


def money(value):
    """Return deterministic PHP cent rounding independent of binary floating-point behavior."""
    return float(_decimal(value, "money").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def administration_fee(transport_subtotal, matrix):
    """Calculate the disclosed fee only on the tax-exclusive transport subtotal."""
    fee = matrix.get("administration_fee") or {}
    if fee.get("basis") != "tax_exclusive_transport_subtotal":
        raise FareMatrixError("unsupported administration-fee basis")
    rate = _decimal(fee.get("rate"), "administration_fee.rate")
    if rate != Decimal("0.10"):
        raise FareMatrixError("MVP administration-fee rate must be exactly 0.10")
    return money(_decimal(transport_subtotal, "transport_subtotal") * rate)


def _validate_matrix(matrix):
    version = str(matrix.get("version") or "").strip()
    if not version:
        raise FareMatrixError("matrix version is required")
    if matrix.get("status") not in ALLOWED_STATUSES:
        raise FareMatrixError(f"matrix {version} has an invalid status")
    if matrix.get("runtime_use") not in {INDICATIVE, "BOOKING_AND_COLLECTION"}:
        raise FareMatrixError(f"matrix {version} has an invalid runtime_use")
    vehicles = matrix.get("vehicles")
    if not isinstance(vehicles, dict) or not vehicles:
        raise FareMatrixError(f"matrix {version} must define vehicles")
    for code, row in vehicles.items():
        if not row.get("category") or row.get("service_class") not in {"STANDARD", "ENGINEERED"}:
            raise FareMatrixError(f"matrix {version} vehicle {code} is invalid")
        if row["service_class"] == "STANDARD":
            for field in ("base", "per_km"):
                if _decimal(row.get(field), f"vehicles.{code}.{field}") < 0:
                    raise FareMatrixError(f"vehicles.{code}.{field} must be non-negative")
            if _decimal(row.get("included_distance_km", 0),
                        f"vehicles.{code}.included_distance_km") < 0:
                raise FareMatrixError(f"vehicles.{code}.included_distance_km must be non-negative")
            if row.get("minimum_charge") is not None and _decimal(
                    row["minimum_charge"], f"vehicles.{code}.minimum_charge") < 0:
                raise FareMatrixError(f"vehicles.{code}.minimum_charge must be non-negative")
    administration_fee(Decimal("1.00"), matrix)
    if matrix["status"] == APPROVED:
        missing = [field for field in REQUIRED_APPROVAL_FIELDS
                   if not str((matrix.get("approval") or {}).get(field) or "").strip()]
        if missing:
            raise FareMatrixError(
                f"approved matrix {version} is missing approval evidence: {', '.join(missing)}")
        if matrix.get("runtime_use") != "BOOKING_AND_COLLECTION":
            raise FareMatrixError("approved collection matrix must declare BOOKING_AND_COLLECTION")
        if (matrix.get("administration_fee") or {}).get("status") != APPROVED:
            raise FareMatrixError("approved matrix must include an approved administration-fee decision")
        rules = matrix.get("commercial_rules") or {}
        unresolved_rules = [key for key in (
            "regional_adjustments", "after_hours_or_peak_pricing", "cancellation_fee",
            "provider_share_basis",
        ) if rules.get(key) is None]
        unresolved_minimums = [code for code, row in vehicles.items()
                               if row["service_class"] == "STANDARD"
                               and row.get("minimum_charge") is None]
        if rules.get("approval_status") != APPROVED or unresolved_rules or unresolved_minimums:
            raise FareMatrixError(
                "approved matrix has unresolved commercial rules or vehicle minimum charges")


def validate_registry(registry):
    """Validate already-parsed registry data and return it unchanged on success."""
    if registry.get("schema_version") != 1 or registry.get("currency") != "PHP":
        raise FareMatrixError("unsupported fare registry schema or currency")
    matrices = registry.get("matrices")
    if not isinstance(matrices, list) or not matrices:
        raise FareMatrixError("fare registry must contain at least one matrix")
    versions = [str(row.get("version") or "") for row in matrices]
    if len(versions) != len(set(versions)):
        raise FareMatrixError("fare matrix versions must be unique and immutable")
    for matrix in matrices:
        _validate_matrix(matrix)
    selected = registry.get("public_estimate_version")
    if selected not in versions:
        raise FareMatrixError("public_estimate_version does not resolve to a matrix")
    return registry


def load_registry(path=None):
    source = Path(path) if path else REGISTRY_PATH
    try:
        registry = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FareMatrixError(f"unable to load fare registry: {exc}") from exc
    return validate_registry(registry)


def get_matrix(version=None, path=None):
    registry = load_registry(path)
    version = version or registry["public_estimate_version"]
    for matrix in registry["matrices"]:
        if matrix["version"] == version:
            return copy.deepcopy(matrix)
    raise FareMatrixError(f"unknown fare matrix version: {version}")


def public_estimate_matrix(path=None):
    """Return the configured estimate matrix, including its unambiguous approval state."""
    return get_matrix(path=path)


def approval_readiness(matrix):
    approval = matrix.get("approval") or {}
    missing = [field for field in REQUIRED_APPROVAL_FIELDS
               if not str(approval.get(field) or "").strip()]
    approved = (matrix.get("status") == APPROVED and
                matrix.get("runtime_use") == "BOOKING_AND_COLLECTION" and not missing)
    return {
        "approved": approved,
        "status": matrix.get("status"),
        "runtime_use": matrix.get("runtime_use"),
        "missing_approval_fields": missing,
    }


def require_collection_approval(matrix):
    """Fail closed before fare-based live collection or provider-settlement activation."""
    readiness = approval_readiness(matrix)
    if not readiness["approved"]:
        raise FareMatrixError(
            "fare matrix is not approved for booking collection; missing or invalid evidence: "
            + ", ".join(readiness["missing_approval_fields"] or [readiness["status"] or "status"]))
    return True


def public_booking_constants(matrix=None):
    """Translate the registry into the legacy runtime shapes used by public_booking."""
    matrix = matrix or public_estimate_matrix()
    vehicle_map = {}
    operational = {}
    for code, row in matrix["vehicles"].items():
        vehicle_map[code] = (
            row["category"], row["service_class"], row.get("base"), row.get("per_km"),
            row.get("inter_island_sea"),
        )
        if row["service_class"] == "STANDARD":
            operational[code] = {
                "additional_stop": row.get("additional_stop", 0),
                "waiting_per_hour": row.get("waiting_per_hour", 0),
                "helper": row.get("helper", 0),
            }
    return vehicle_map, operational
