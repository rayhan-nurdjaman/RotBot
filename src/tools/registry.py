import inspect
import logging
from dataclasses import dataclass
from typing import Any, get_type_hints

from pydantic import ConfigDict, ValidationError, create_model

logger = logging.getLogger(__name__)

# What a tool handler is allowed to touch. Handlers receive this instead of importing client or main.
# instance: the chat's Instance. client: anything with send_message, send_image and typing (the client module).
@dataclass
class ToolContext:
	instance: Any
	client: Any

	@property
	def chat_id(self):
		return self.instance.unique_id

# Raise from a handler for an expected failure. The message goes back to the model as the result.
class ToolError(Exception):
	pass

def _validation_message(error):
	return '; '.join(f'{".".join(str(part) for part in e["loc"])}: {e["msg"]}' for e in error.errors())

class Registry:
	def __init__(self):
		self._tools = {}
		# Tools OpenRouter runs itself (type 'openrouter:...'). Declared in the request, no handler here.
		self._server_tools = {}

	# Registers an async function as a tool. The first parameter is the ToolContext; every other parameter is
	# an argument the model supplies. Arguments need a type hint and no default, because strict schemas list every
	# property as required. Use Annotated[type, Field(description=...)] to describe an argument.
	# The function's docstring becomes the tool description. The handler returns a dict, which is sent to the
	# model as {'ok': True, ...}. Raise ToolError for expected failures.
	def tool(self, fn):
		name = fn.__name__
		if name in self._tools:
			raise ValueError(f'Tool \'{name}\' is already registered.')
		description = inspect.getdoc(fn)
		if not description:
			raise ValueError(f'Tool \'{name}\' needs a docstring, it is the description the model sees.')
		hints = get_type_hints(fn, include_extras=True)
		fields = {}
		for parameter in list(inspect.signature(fn).parameters.values())[1:]:
			if parameter.default is not inspect.Parameter.empty:
				raise ValueError(f'Tool \'{name}\' argument \'{parameter.name}\' has a default, strict schemas do not allow that.')
			if parameter.name not in hints:
				raise ValueError(f'Tool \'{name}\' argument \'{parameter.name}\' needs a type hint.')
			fields[parameter.name] = (hints[parameter.name], ...)
		model = create_model(name, __config__=ConfigDict(extra='forbid'), **fields)

		schema = model.model_json_schema()
		schema.pop('title', None)
		for property_schema in schema.get('properties', {}).values():
			property_schema.pop('title', None)
		schema['properties'] = schema.get('properties', {})
		schema['required'] = list(schema['properties'])
		schema['additionalProperties'] = False

		self._tools[name] = {
			'handler': fn,
			'model': model,
			'description': description,
			'schema': {
				'type': 'function',
				'function': {'name': name, 'description': description, 'strict': True, 'parameters': schema},
			},
		}
		return fn

	# Declares an OpenRouter server tool, e.g. server_tool('openrouter:web_search', 'Searches the web.', max_uses=3).
	# The model calls it and OpenRouter runs it inside the same request, so the result never reaches our code and
	# is not part of the history we replay. `description` is only used for the system prompt's Tools section.
	def server_tool(self, type, description, **parameters):
		name = type.split(':', 1)[1]
		if name in self._tools or name in self._server_tools:
			raise ValueError(f'Tool \'{name}\' is already registered.')
		schema = {'type': type}
		if parameters:
			schema['parameters'] = parameters
		self._server_tools[name] = {'description': description, 'schema': schema}

	# The list sent to the model as `tools`: function tools, then server tools, each in registration order.
	# The order is part of the cached prompt prefix, so it must not change while running.
	def schemas(self):
		return [tool['schema'] for tool in (*self._tools.values(), *self._server_tools.values())]

	# Numbered list for the "Tools" section of the system prompt, built from the docstrings. Static for the
	# lifetime of the process, so it is safe inside the cached system prompt.
	def prompt_section(self):
		entries = []
		for number, (name, tool) in enumerate((*self._tools.items(), *self._server_tools.items()), start=1):
			lines = '\n'.join(f'- {line}' for line in tool['description'].splitlines() if line.strip())
			entries.append(f'{number}. {name}\n{lines}')
		return '\n'.join(entries)

	# Never raises for a bad call. Unknown tools and bad arguments are returned to the model as {'ok': False, ...}.
	# Unexpected exceptions from a handler propagate, so the caller can log them with a traceback.
	async def call(self, name, arguments, ctx):
		tool = self._tools.get(name)
		if tool is None:
			logger.warning(f'Model called unknown tool \'{name}\'.')
			return {'ok': False, 'error': f'Unknown tool \'{name}\'.'}
		try:
			arguments = tool['model'](**arguments).model_dump()
		except (ValidationError, TypeError) as e:
			message = _validation_message(e) if isinstance(e, ValidationError) else str(e)
			return {'ok': False, 'error': f'Invalid arguments: {message}'}
		try:
			result = await tool['handler'](ctx, **arguments)
		except ToolError as e:
			return {'ok': False, 'error': str(e)}
		return {'ok': True, **result}

registry = Registry()
tool = registry.tool
