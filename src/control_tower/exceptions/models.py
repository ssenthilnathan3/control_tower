from enum import Enum


class ExceptionStatus(str, Enum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    RESOLVED = "RESOLVED"
    REOPENED = "REOPENED"


class ExceptionRole(str, Enum):
    SYSTEM = "SYSTEM"
    OPERATOR = "OPERATOR"
    APPROVER = "APPROVER"


class ExceptionAction(str, Enum):
    DETECTED = "DETECTED"
    ASSIGNED = "ASSIGNED"
    INVESTIGATION_STARTED = "INVESTIGATION_STARTED"
    RESOLUTION_REQUESTED = "RESOLUTION_REQUESTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REOPENED = "REOPENED"


class ExceptionWorkflowError(ValueError):
    pass
