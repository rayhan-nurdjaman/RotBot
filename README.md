# RotBot

A general client Interface for LLMs (currently ChatGPT on telegram).

This project consists of a couple of modules
- Client: This takes care of everything frontend, currently its telegram.
- Assistant: This takes care of everything backend, currently its chatgpt.

# Privacy
Every message the bot can see in a chat, including group messages and photos, is sent to a third-party model provider (currently OpenAI) to generate replies. Message text and replies are also written to `app.log` by the application logger.

# TODO
- Remove any mention of chat logging or data collection, not important right now.
- casino.py also deprecated, move to a seperate folder for now and revisit later and develop the api interface instead
- Research open router api
- Figure out any good search tool providers/mcp/whatnot
- make a calendar tool etc and avoid the current poll pattern in use
- disable always on, it is not applicable anymore
  - if not: replace the current model with jev via open router
  - as fallback, always reply 5 mins (stored in a variable) after the last message has been sent to support multiple texts. consider vector style time increments (30 sec -> 1 min -> 2 min) for every consecutive message, though realistically assumptions dont justify that method
- current architecture is bad, need to change everything
- when changing personalities, consider compacting previous prompts and starting fresh with only the system prompt and the compacted context to avoid accidentally few shot prompting the model
- improve memory or make it a blackboard kinda thing. when the user sends an image for an event or a text snippet with no other context, based on a memo created like "any random snippet of dates or events the user sends must be added as a calendar reminder, always follow up to the user if details are vague"
- google calendar api integration maybe?
