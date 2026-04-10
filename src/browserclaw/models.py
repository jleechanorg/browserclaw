from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class BrowserStep:
    action: str
    selector: str | None = None
    value: str | None = None
    url: str | None = None
    milliseconds: int | None = None


@dataclass(slots=True)
class EndpointSignature:
    name: str
    method: str
    url_template: str
    host: str
    query_keys: list[str] = field(default_factory=list)
    request_header_keys: list[str] = field(default_factory=list)
    request_body_keys: list[str] = field(default_factory=list)
    response_header_keys: list[str] = field(default_factory=list)
    sample_status_codes: list[int] = field(default_factory=list)
    sample_content_types: list[str] = field(default_factory=list)
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EndpointCatalog:
    site: str
    source_har: str
    notes: list[str]
    endpoints: list[EndpointSignature]
    llm_provider: str | None = None
    llm_model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "site": self.site,
            "source_har": self.source_har,
            "notes": list(self.notes),
            "llm_provider": self.llm_provider,
            "llm_model": self.llm_model,
            "endpoints": [endpoint.to_dict() for endpoint in self.endpoints],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "EndpointCatalog":
        endpoints = [EndpointSignature(**item) for item in payload.get("endpoints", [])]
        return cls(
            site=payload["site"],
            source_har=payload["source_har"],
            notes=list(payload.get("notes", [])),
            endpoints=endpoints,
            llm_provider=payload.get("llm_provider"),
            llm_model=payload.get("llm_model"),
        )

