from typing import NamedTuple

# Pinned OpenRouter slugs. The `~anthropic/claude-*-latest` aliases are avoided on purpose:
# they move to new versions (with behaviour changes) without notice.
MODELS = {
	'haiku-5.5':	'anthropic/claude-haiku-5.5',
	'sonnet-5.5':	'anthropic/claude-sonnet-5.5',
	'opus-5.5':		'anthropic/claude-opus-5.5',
}

# Every effort level the 5.5 models accept on OpenRouter.
EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')

# Model and reasoning effort are chosen together, e.g. `haiku-5.5-high`.
# Defaults to the cheapest model at the lowest effort.
DEFAULT_NAME	= 'haiku-5.5'
DEFAULT_EFFORT	= 'low'

class ModelSpec(NamedTuple):
	name: str
	effort: str

	@property
	def slug(self):
		return MODELS[self.name]

	def __str__(self):
		return f'{self.name}-{self.effort}'

DEFAULT_MODEL = ModelSpec(DEFAULT_NAME, DEFAULT_EFFORT)

# Parses `<model>` or `<model>-<effort>`. Raises ValueError for anything unknown.
def parse_model(text):
	text = text.strip().lower()
	name, _, effort = text.rpartition('-')
	if name in MODELS and effort in EFFORTS:
		return ModelSpec(name, effort)
	if text in MODELS:
		return ModelSpec(text, DEFAULT_EFFORT)
	raise ValueError(f'Unknown model \'{text}\'.')
