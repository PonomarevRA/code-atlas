"""Read-only imported test evidence; never a runner or proof of execution."""

from .junit import FeedbackError, JUnitReport, parse_junit_report
from .package import build_feedback_package

__all__ = ["FeedbackError", "JUnitReport", "parse_junit_report", "build_feedback_package"]
