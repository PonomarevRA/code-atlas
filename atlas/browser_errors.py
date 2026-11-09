"""Stable recovery fields shared by browser HTTP and MCP; no response text is interpreted."""


class BrowserSessionError(ValueError):
    """A public browser error with allowlisted recovery; never use captured input as its message."""
    def __init__(self, message, status=404):
        super().__init__(message)
        self.status = status
        self.code, self.retryable, self.recovery_action = {
            404: ('browser-item-unavailable', False, 'select-live-session-or-node'),
            410: ('browser-capture-expired', True, 'capture-again'),
            429: ('browser-capacity', True, 'finish-owned-session'),
            503: ('browser-busy', True, 'retry-later'),
            413: ('browser-response-budget', False, 'narrow-page'),
            424: ('browser-privacy-unavailable', False, 'check-privacy-settings'),
            422: ('browser-invalid-request', False, 'check-arguments'),
            504: ('browser-timeout', True, 'inspect-owned-session-before-retry'),
            502: ('atlas-unavailable', True, 'start-atlas'),
        }.get(status, ('browser-request-rejected', False, 'check-arguments'))

    def public(self):
        """Expose bounded recovery facts without captured values or parser exception strings."""
        return {'error': str(self), 'code': self.code, 'retryable': self.retryable,
                'recoveryAction': self.recovery_action}
