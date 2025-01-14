import os
import openai

api_key = os.getenv('OPENAI_API_KEY')
openai_client = openai.AsyncOpenAI(api_key=api_key)

async def get_url(prompt):
	try:
		response = await openai_client.images.generate(
			model="dall-e-3",
			prompt=prompt,
			size="1024x1024",
			quality="standard",
			n=1,
		)
		image_url = response.data[0].url
		return image_url
	except:
		return None