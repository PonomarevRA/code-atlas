"""Public bounded errors for temporary checkout-map lifetime and storage guards."""


class CheckoutMapError(ValueError):
    """Carry a stable code/status without reflecting private index contents."""

    def __init__(self, message, status=400, code='checkout_map_invalid'):
        super().__init__(message)
        self.status, self.code = status, code
