import openai
import os
import numpy as np
import chat_completion as ChatCompletion

system_prompt = [
    {
        'role' : 'system',
        'content' : [
            {
                'type': 'text',
                'text': '''
                    Task: Determine whether the assistant should respond to the user input.

                    Assistant Name: RotBot (username: `rotrot_botbot`), also known simply as Rot.

                    Rules for Response:

                    - The assistant should only respond if the user directly addresses RotBot.
                    - If the message is directed to RotBot or asks RotBot a question, respond with "Yes".
                    - If the message is not directed to RotBot (e.g., someone talking to another person or someone simply stating a fact), respond with "No".

                    Message Format:

                    username_1: Hi RotBot! // Yes
                    rotrot_botbot: Hello! How can I assist you? // Response will be generated after you respond with 'Yes' 
                    username_2: Can you ask Rot how to do my math? // No
                    username_1: Yeah of course! // No
                    username_1: Um... // No
                    username_1: Rot, how does algebra work? // Yes
                    rotrot_botbot: Algebra is a branch of mathematics that deals with symbols and the rules for manipulating those symbols. // Response will be generated after you respond with 'Yes'

                    Final Output: Provide a single-word response: "Yes" or "No".
                '''
            }
        ]
    }
]

def format_messages(context):
    res = ''
    for message in context:
        if 'content' not in message:
            continue
        if message['role'] == 'system':
            continue
        message_prefix = 'rotrot_botbot: ' if message['role'] == 'assistant' else ''
        for content in message['content']:
            if content['type'] != 'text':
                continue
            res += f'{message_prefix}{content['text']}\n'
    return res

# Uses LLMs
async def should_respond(context):
    query = [
        {
            'role': 'user',
            'content': [
                {
                    'type': 'text',
                    'text': f'Based on the following messages, should RotBot respond?:\n{format_messages(context)}'
                }
            ]
        }
    ]

    response = await ChatCompletion.create(system_prompt+query, 'gpt-4o-mini')
    return response.message.content == 'Yes'

# bias = 0

# synthetic_positives = [
#     "Hey Rot, what’s the weather like today?",
#     "Yo @rotrot_botbot! What’s up? Got any fun jokes for me?",
#     "RotBot, can you help me with my homework? I need to find the capital of Mongolia.",
#     "Rot, do you ever wonder what it’s like to be human?",
#     "Hey @rotrot_botbot, how do I fix this error in Python? It says 'IndexError: list index out of range.'",
#     "RotBot, I’m feeling kinda lost lately. Any wise words for me?",
#     "Rot, who would win in a fight: Batman or Iron Man?",
#     "@rotrot_botbot, I need to buy a new phone. What’s the best option under $500?",
#     "Rot, why is the sky blue?",
#     "Yo RotBot, got any tips for leveling up quickly in Elden Ring?",
#     "Hey RotBot, can you recommend a good book to read?",
#     "@rotrot_botbot, what’s 123 multiplied by 456?",
#     "Rot, can you tell me a random fun fact about space?",
#     "Hey RotBot, how do I make the perfect cup of coffee?",
#     "@rotrot_botbot, can you translate 'Hello' into Japanese?",
#     "Rot, do you think AI will ever take over the world?",
#     "Hey RotBot, what’s the best way to lose weight without dieting?",
#     "Rot, can you help me find a good recipe for chocolate cake?",
#     "@rotrot_botbot, what’s the most visited city in the world?",
#     "RotBot, how do I write a polite email to my professor?",
#     "Hey Rot, what time is it in Tokyo right now?",
#     "@rotrot_botbot, can you generate a random number for me?",
#     "Rot, how do I fix my Wi-Fi when it keeps disconnecting?",
#     "RotBot, can you summarize the latest news for me?",
#     "Hey @rotrot_botbot, what’s the difference between a comet and an asteroid?",
#     "Rot, what’s your favorite type of music?",
#     "Hey RotBot, I need a workout plan for beginners. Any suggestions?",
#     "@rotrot_botbot, who invented the light bulb?",
#     "Rot, can you recommend a good movie for a Friday night?",
#     "RotBot, what’s the best way to start learning a new language?"
# ]

# synthetic_negatives = [
#     "Hey everyone, what time are we meeting tomorrow?",
#     "Did anyone finish the assignment? I’m stuck on question 5.",
#     "Happy birthday, Alex! 🎉 Hope you’re having an amazing day!",
#     "Guys, I just saw the funniest meme, you HAVE to check this out 😂.",
#     "Don’t forget, the potluck is this Saturday. What’s everyone bringing?",
#     "Anyone else having trouble with the WiFi, or is it just me?",
#     "Thanks for the ride earlier, Sam. You’re a lifesaver!",
#     "What movie are we watching for the next movie night? Any suggestions?",
#     "OMG, did you guys see the news today? That was wild!",
#     "Let’s plan a game night soon. I miss hanging out with all of you!"
# ]

# embeddings = []

# for text in synthetic_positives:
#     response = openai_client.embeddings.create(
#         input=text,
#         model="text-embedding-3-small"  # Example model; adjust as needed
#     )
#     embeddings.append(response.data[0].embedding)  

# embeddings_array = np.array(embeddings)

# average_embedding = np.mean(embeddings_array, axis=0)

# norm = np.linalg.norm(average_embedding)

# if norm > 0:  # To avoid division by zero
#     reference_embedding = average_embedding / norm
# else:
#     reference_embedding = average_embedding  

# positive_response = openai_client.embeddings.create(
#     input='no wrong one rot, try directly searching for tomato cat',
#     model="text-embedding-3-small"  # Example model; adjust as needed
# )

# negative_response = openai_client.embeddings.create(
#     input='No, wrong one Rot. Try directly searching for \'Tomato Cat\'',
#     model="text-embedding-3-small"  # Example model; adjust as needed
# )

# positive_cosine_similarity = np.dot(np.array(positive_response.data[0].embedding), reference_embedding)
# negative_cosine_similarity = np.dot(np.array(negative_response.data[0].embedding), reference_embedding)
