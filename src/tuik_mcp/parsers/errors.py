class ParseError(ValueError):
    """The file could not be parsed using the selected format."""


class InvalidFiltersError(ParseError):
    """A requested column does not exist in the parsed table."""


class UnsupportedFormatError(ParseError):
    """No parser has been registered for this file format."""
