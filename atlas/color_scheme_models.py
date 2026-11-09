"""Bounded declarative palettes; CSS, URLs and executable code are not theme values."""
from typing import Literal
from pydantic import BaseModel, Field, field_validator

TOKEN_NAMES = ('background', 'surface', 'surfaceAlt', 'text', 'muted', 'border', 'accent', 'accentSoft', 'onAccent', 'focus', 'canvas', 'dot', 'card', 'cardHeader', 'cardBorder', 'edge', 'selection', 'control', 'controlText', 'waiting', 'waitingText', 'warning', 'warningSoft', 'error', 'errorSoft', 'keyword', 'string', 'number', 'comment', 'code', 'type')
Token = Literal['background', 'surface', 'surfaceAlt', 'text', 'muted', 'border', 'accent', 'accentSoft', 'onAccent', 'focus', 'canvas', 'dot', 'card', 'cardHeader', 'cardBorder', 'edge', 'selection', 'control', 'controlText', 'waiting', 'waitingText', 'warning', 'warningSoft', 'error', 'errorSoft', 'keyword', 'string', 'number', 'comment', 'code', 'type']


class ColorScheme(BaseModel):
    """A partial palette inherits every omitted semantic token from its light/dark base."""
    id: str = Field(pattern=r'^[a-z][a-z0-9-]{0,47}$')
    label: str = Field(min_length=1, max_length=80)
    base: Literal['light', 'dark'] = 'light'
    tokens: dict[Token, str] = Field(default_factory=dict, max_length=len(TOKEN_NAMES))
    model_config = {'extra': 'forbid'}

    @field_validator('tokens')
    @classmethod
    def hex_colors(cls, tokens):
        """Only opaque hex colors can reach CSS generation; reject URLs and declarations."""
        import re
        if any(not re.fullmatch(r'#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?', value) for value in tokens.values()):
            raise ValueError('Colors must be #RGB or #RRGGBB')
        return tokens
