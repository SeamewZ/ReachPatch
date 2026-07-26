from __future__ import annotations

from dataclasses import replace
from typing import Any

from reachpatch.models.base import content_hash
from reachpatch.models.controller import TransitionCertificate


def certificate_hash(fields: dict[str, Any]) -> str:
    payload = dict(fields)
    payload.pop("recomputation_hash", None)
    return content_hash(payload)


def finalize_certificate(
    certificate: TransitionCertificate,
) -> TransitionCertificate:
    return replace(
        certificate,
        recomputation_hash=certificate_hash(certificate.to_dict()),
    )


def verify_transition_certificate(certificate: TransitionCertificate) -> bool:
    return certificate.recomputation_hash == certificate_hash(certificate.to_dict())
