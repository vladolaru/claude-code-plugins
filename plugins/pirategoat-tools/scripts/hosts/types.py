"""Data types for host-context discovery."""

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Literal, Optional, Any


HostKind = Literal["runtime-host", "library-dep"]
ResolverSource = Literal[
    "explicit", "wp-env", "docker-compose", "sibling",
    "ecosystem-cache", "vendor-inspection", "install-cache",
]
Confidence = Literal["low", "medium", "high"]
BannerReason = Literal["partial_unresolved", "fully_unavailable", "install_failed"]


@dataclass
class HostEntry:
    name: str
    kind: HostKind
    path: str
    source: ResolverSource
    version: Optional[str] = None
    version_freshness: Optional[str] = None
    confidence: Confidence = "medium"
    notes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HostEntry":
        return cls(**data)


@dataclass
class Banner:
    degraded: bool
    reason: Optional[BannerReason]
    message: str
    unresolved: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Banner":
        return cls(**data)


@dataclass
class HostContextManifest:
    version: int
    resolved: List[HostEntry] = field(default_factory=list)
    unresolved: List[Dict[str, Any]] = field(default_factory=list)
    banner: Optional[Banner] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "resolved": [e.to_dict() for e in self.resolved],
            "unresolved": list(self.unresolved),
            "banner": self.banner.to_dict() if self.banner else None,
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "HostContextManifest":
        return cls(
            version=data.get("version", 1),
            resolved=[HostEntry.from_dict(e) for e in data.get("resolved", [])],
            unresolved=list(data.get("unresolved", [])),
            banner=Banner.from_dict(data["banner"]) if data.get("banner") else None,
            diagnostics=dict(data.get("diagnostics", {})),
        )
