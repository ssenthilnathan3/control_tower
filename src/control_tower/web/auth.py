import json
import os
from dataclasses import dataclass

from fastapi import Header, HTTPException, status

from control_tower.exceptions import ExceptionRole


@dataclass(frozen=True)
class Principal:
    actor: str
    role: ExceptionRole


def load_identities() -> dict[str, Principal]:
    raw = os.getenv("CONTROL_TOWER_IDENTITIES_JSON", "{}")
    values = json.loads(raw)
    return {
        token: Principal(str(value["actor"]), ExceptionRole(value["role"]))
        for token, value in values.items()
    }


class Authenticator:
    def __init__(self, identities: dict[str, Principal]):
        self.identities = identities

    def authenticate(
        self, authorization: str | None = Header(default=None)
    ) -> Principal:
        scheme, _, token = (authorization or "").partition(" ")
        principal = self.identities.get(token) if scheme.lower() == "bearer" else None
        if principal is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="valid bearer token required",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return principal


def require_role(principal: Principal, *roles: ExceptionRole) -> None:
    if principal.role not in roles:
        raise HTTPException(status_code=403, detail="role is not permitted")
