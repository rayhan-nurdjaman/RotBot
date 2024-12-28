import random
import os
import json
import sqlite3

class Casino:
	def __init__(self):
		self.aura_balances = {}
		self._init_data()

	def get_balance(self, user_id):
		if not user_id in self.aura_balances:
			self.aura_balances[user_id] = 0
		return self.aura_balances[user_id]

	def modify_balance(self, user_id, amount):
		if not user_id in self.aura_balances:
			self.aura_balances[user_id] = 0
		self.aura_balances[user_id] += amount
		self._dump()

	def _dump(self) -> None:
		with open('data/aura_balances.json', 'w') as file:
			file.write(json.dumps(self.aura_balances, indent=4))

	def _init_data(self):
		# TODO: Change this to param in constructor
		# con = sqlite3.connect("data/database.db")
		# cur = con.cursor()
		# cur.execute("CREATE TABLE movie(title, year, score)")
		
		if not os.path.exists('data'):
			os.makedirs('data')
		try:
			with open('data/aura_balances.json', 'r') as balances_file:
				raw_aura_balances = balances_file.read()
				self.aura_balances = json.loads(raw_aura_balances) if raw_aura_balances else {}
		except FileNotFoundError:
			with open('data/aura_balances.json', 'w') as balances_file:
				balances_file.write(json.dumps({}))
				self.aura_balances = {}

	def dice(n):
		random.randint(0, 12)