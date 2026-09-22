class ValhallaError(Exception):
    pass


class ValhallaCmdError(ValhallaError):
    pass


class PyPiError(ValhallaCmdError):
    """
    Anything that went wrong while resolving the interpreter/pip or while
    installing a PyPI package (core/pypi.py). ``str(e)`` stays short enough for
    a message bar; ``e.detail`` carries the full output for the log panel.
    """

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail or message
