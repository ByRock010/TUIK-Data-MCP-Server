"""The supported Word template: plain header and footer text with fixed placeholders (#95).

It has no dependency on python-docx, so the tool schema can describe it even
when the optional docx-out extra is not installed.
"""

import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TEMPLATE_TEXT = 200
PLACEHOLDER = re.compile(r"\{([^{}]*)\}")
PLACEHOLDERS = ("title", "report_id", "status", "cutoff", "organization")


class DocxTemplate(BaseModel):
    """The supported template: plain header and footer text with fixed placeholders.

    Placeholders are `{title}`, `{report_id}`, `{status}`, `{cutoff}` and
    `{organization}`. Anything else in braces, fields, styles, macros or a Word
    template file is not supported and is refused.
    """

    model_config = ConfigDict(extra="forbid")

    organization: str | None = Field(default=None, max_length=MAX_TEMPLATE_TEXT)
    header: str | None = Field(default=None, max_length=MAX_TEMPLATE_TEXT)
    footer: str | None = Field(default=None, max_length=MAX_TEMPLATE_TEXT)

    @field_validator("organization", "header", "footer")
    @classmethod
    def plain_text(cls, value: str | None, info):
        if value is None:
            return value
        if any(unicodedata.category(char) in {"Cc", "Cf", "Cs"} for char in value):
            raise ValueError(f"{info.field_name} may not contain control or format characters")
        if info.field_name == "organization":
            if "{" in value or "}" in value:
                raise ValueError("organization is plain text without placeholders")
            return value
        unknown = sorted({name for name in PLACEHOLDER.findall(value) if name not in PLACEHOLDERS})
        if unknown:
            raise ValueError(
                f"Unsupported template placeholder {{{unknown[0]}}}; supported: "
                + ", ".join(f"{{{name}}}" for name in PLACEHOLDERS)
            )
        if PLACEHOLDER.sub("", value).count("{") or PLACEHOLDER.sub("", value).count("}"):
            raise ValueError(f"{info.field_name} has an unmatched brace")
        return value
