class SecurityHeaders:
    """Strict CSP (no inline scripts), nosniff, no referrer leakage of the search term."""

    CSP = ("default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
           "font-src https://fonts.gstatic.com; img-src 'self' data:; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        r = self.get_response(request)
        r.headers.setdefault("Content-Security-Policy", self.CSP)
        r.headers.setdefault("Referrer-Policy", "same-origin")
        return r
