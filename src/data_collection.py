with open('data/chat.txt', 'a') as file:
	file.write('INIT\n')

def add_user_message(text):
	with open('data/chat.txt', 'a') as file:
		file.write('USER: ' + text + '\n')

def add_assistant_message(text):
	with open('data/chat.txt', 'a') as file:
		file.write('ASSISTANT: ' + text + '\n')