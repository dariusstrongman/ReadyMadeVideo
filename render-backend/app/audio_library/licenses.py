"""Central legal eligibility policy. Descriptive tags never affect licensing."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field, HttpUrl

from .models import LicenseDecision, ProviderAsset

POLICY_VERSION = "audio-license-policy-v1"


class ProprietaryLicenseApproval(BaseModel):
    licenseId: str = Field(min_length=2)
    licenseName: str = Field(min_length=2)
    licenseUrl: HttpUrl
    commercialUseAllowed: bool
    modificationAllowed: bool
    attributionRequired: bool
    approvalReference: str = Field(min_length=3)
    reviewedAt: str = Field(min_length=10)


def _canonical_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    scheme = "https" if parsed.scheme in {"http", "https"} else parsed.scheme
    path = parsed.path.rstrip("/") + "/"
    return urlunsplit((scheme, parsed.netloc.lower(), path, "", ""))


class LicensePolicy:
    """Fail-closed policy for commercial, modifiable audiovisual use."""

    def __init__(self, approvals: list[ProprietaryLicenseApproval] | None = None):
        self.approvals = approvals or []

    @classmethod
    def from_file(cls, path: Path) -> LicensePolicy:
        if not path.exists():
            return cls()
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls([ProprietaryLicenseApproval(**item)
                    for item in data.get("approvedLicenses", [])])

    def _reject(self, asset: ProviderAsset, code: str, reason: str) -> LicenseDecision:
        return LicenseDecision(
            accepted=False, reasonCode=code, reason=reason,
            licenseName=asset.licenseName,
            licenseUrl=str(asset.licenseUrl) if asset.licenseUrl else None,
            policyVersion=POLICY_VERSION,
        )

    def evaluate(self, asset: ProviderAsset) -> LicenseDecision:
        terms = asset.providerTerms
        if not terms.ingestionMethodAllowed:
            return self._reject(
                asset, "provider_ingestion_not_allowed",
                "Provider terms do not permit this ingestion method",
            )
        if not terms.commercialApiUseAllowed:
            return self._reject(
                asset, "provider_commercial_use_not_approved",
                "Commercial use of this provider access method is not approved",
            )
        if not terms.approvalReference:
            return self._reject(
                asset, "missing_provider_approval_reference",
                "Provider terms approval must have an auditable reference",
            )
        if not asset.licenseUrl:
            return self._reject(asset, "missing_license_metadata", "License URL is required")
        if not asset.creatorName:
            return self._reject(asset, "missing_creator_metadata", "Creator name is required")
        license_url = _canonical_url(str(asset.licenseUrl))
        name = asset.licenseName.strip().lower()
        if any(token in name or token in license_url for token in (
            "noncommercial", "by-nc", "/nc/", "sampling+", "samplingplus",
        )):
            return self._reject(
                asset, "noncommercial_license",
                "Noncommercial and Sampling+ assets are not production eligible",
            )
        if any(token in name or token in license_url for token in (
            "no derivatives", "no-derivatives", "by-nd", "/nd/",
        )):
            return self._reject(
                asset, "no_derivatives_license",
                "The license does not permit required editing and modification",
            )
        if asset.declaredCommercialUseAllowed is False:
            return self._reject(
                asset, "commercial_use_denied", "Provider metadata denies commercial use",
            )
        if asset.declaredModificationAllowed is False:
            return self._reject(
                asset, "modification_denied", "Provider metadata denies modification",
            )

        if license_url == "https://creativecommons.org/publicdomain/zero/1.0/":
            return LicenseDecision(
                accepted=True, reasonCode="accepted_cc0",
                reason="CC0 permits commercial use and modification",
                normalizedLicenseId="cc0-1.0", licenseName="CC0 1.0",
                licenseUrl=license_url, attributionRequired=False,
                attributionText=asset.attributionText,
                commercialUseAllowed=True, modificationAllowed=True,
                approvalReference="policy:cc0-1.0",
                policyVersion=POLICY_VERSION,
            )

        if license_url in {
            "https://creativecommons.org/licenses/by/3.0/",
            "https://creativecommons.org/licenses/by/4.0/",
        }:
            if not asset.attributionText or not asset.creatorUrl:
                return self._reject(
                    asset, "missing_attribution_metadata",
                    "CC BY requires complete creator, creator URL, and attribution text",
                )
            version = "4.0" if "/4.0/" in license_url else "3.0"
            return LicenseDecision(
                accepted=True, reasonCode="accepted_cc_by",
                reason="CC BY permits commercial modification with attribution",
                normalizedLicenseId=f"cc-by-{version}", licenseName=f"CC BY {version}",
                licenseUrl=license_url, attributionRequired=True,
                attributionText=asset.attributionText,
                commercialUseAllowed=True, modificationAllowed=True,
                approvalReference=f"policy:cc-by-{version}",
                policyVersion=POLICY_VERSION,
            )

        approval = next((item for item in self.approvals
                         if item.licenseName.strip().lower() == name
                         and _canonical_url(str(item.licenseUrl)) == license_url), None)
        if not approval:
            return self._reject(
                asset, "unknown_license",
                "License is not in the production-safe policy or approval registry",
            )
        if not approval.commercialUseAllowed:
            return self._reject(asset, "commercial_use_denied", "Approval denies commercial use")
        if not approval.modificationAllowed:
            return self._reject(asset, "modification_denied", "Approval denies modification")
        if approval.attributionRequired and (not asset.attributionText or not asset.creatorUrl):
            return self._reject(
                asset, "missing_attribution_metadata",
                "Approved license requires complete attribution metadata",
            )
        return LicenseDecision(
            accepted=True, reasonCode="accepted_approved_commercial_license",
            reason=f"Matched approval registry reference {approval.approvalReference}",
            normalizedLicenseId=approval.licenseId,
            licenseName=approval.licenseName, licenseUrl=license_url,
            attributionRequired=approval.attributionRequired,
            attributionText=asset.attributionText,
            commercialUseAllowed=True, modificationAllowed=True,
            approvalReference=approval.approvalReference,
            policyVersion=POLICY_VERSION,
        )
