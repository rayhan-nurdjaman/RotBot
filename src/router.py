from models import DEFAULT_MODEL

# Stub. Picks the model (and reasoning effort) for a turn when a chat has auto-routing enabled.
#
# TODO: replace the body with a classifier that reads the conversation and chooses the appropriate
# model. Keep the signature: it receives the Instance (context, personality, settings) and must return
# a models.ModelSpec. Until then it always returns the cheapest model.
def route(instance):
	match instance:
		case _:
			return DEFAULT_MODEL
