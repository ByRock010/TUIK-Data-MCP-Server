class ParseError(Exception):
    """The file could not be parsed using the selected format."""


class InvalidFiltersError(ParseError):
    """A requested column does not exist in the parsed table."""


class UnsupportedFormatError(ParseError):
    """No parser has been registered for this file format."""


class ParseLimitError(ParseError):
    """The file exceeds a configured parsing budget."""
