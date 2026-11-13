"""Public bounded errors for temporary checkout-map lifetime and storage guards."""


class CheckoutMapError(ValueError):
    """Carry a stable code/status without reflecting private index contents."""

    def __init__(self, message, status=400, code='checkout_map_invalid'):
        super().__init__(message)
        self.status, self.code = status, code


class CheckoutMapBuilding(CheckoutMapError):
    """An unpinned search reached a build before its first snapshot was published."""

    def __init__(self, session_id, project_id, build_status, job_id):
        """Carry only the bounded facts needed to continue the same session."""
        message = 'Карта строится. Проверьте состояние или найдите объявление по файлу.'
        super().__init__(message, 409, 'CHECKOUT_MAP_BUILDING')
        self.details = {
            'state': 'building', 'checkoutSessionId': session_id, 'projectId': project_id,
            'temporary': True, 'buildStatus': build_status, 'jobId': job_id, 'retryAfterMs': 1000,
        }
