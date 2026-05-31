class PayoutRequestError(ValueError):
    status_code = 400
    code = "PAYOUT_REQUEST_ERROR"

    def __init__(self, message, *, code=None, status_code=None):
        super().__init__(message)
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code


class PayoutConflictError(PayoutRequestError):
    status_code = 409
    code = "PAYOUT_EXTERNAL_ID_CONFLICT"


class PayoutResourceUnavailableError(PayoutRequestError):
    status_code = 503
    code = "PAYOUT_RESOURCE_UNAVAILABLE"


class PayoutDestinationNotActivatedError(PayoutRequestError):
    status_code = 400
    code = "DESTINATION_NOT_ACTIVATED"
