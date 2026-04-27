"""Base class for host resolvers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Any

from hosts.types import HostEntry


@dataclass
class ResolverResult:
    entries: List[HostEntry]
    unresolved: List[Dict[str, Any]]
    notes: Dict[str, Any]


class HostResolver(ABC):
    """A resolver discovers host codebases by one mechanism.

    Implementations must be side-effect-free: no network, no git, no mutation.
    Read-only filesystem introspection of repo_path and well-known dirs only.
    """

    source: str  # ResolverSource literal; set on subclass

    @abstractmethod
    def resolve(self, repo_path: str) -> ResolverResult:
        """Return what this resolver found. Empty result is fine."""
        raise NotImplementedError
