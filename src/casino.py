import os
import random
import sqlite3

def get_balance(user_id):
	with sqlite3.connect("data/database.db") as con:
		cur = con.cursor()
		res = cur.execute("SELECT aura FROM users WHERE user_id = ?", (user_id,))
		row = res.fetchone()
		if row == None:
			cur.execute("INSERT INTO users (user_id, aura) VALUES (?, ?)", (user_id, 0))
			con.commit()
			return 0
		return row[0]

def modify_balance(user_id, amount):
	aura = get_balance(user_id)
	aura += amount
	with sqlite3.connect("data/database.db") as con:
		cur = con.cursor()
		cur.execute("UPDATE users SET aura = ? WHERE user_id = ?", (aura, user_id))
		con.commit()

def get_top(top=5):
	with sqlite3.connect("data/database.db") as con:
		cur = con.cursor()
		res = cur.execute("SELECT user_id, aura FROM users ORDER BY aura DESC LIMIT ?", (top,))
		rows = res.fetchall()
		return [(row[0], row[1]) for row in rows]

def _init_data():
	os.makedirs("data", exist_ok=True)
	with sqlite3.connect("data/database.db") as con:
		cur = con.cursor()
		res = cur.execute("SELECT name FROM sqlite_master WHERE name = 'users'")
		row = res.fetchone()
		# Create table if doesn't exist
		if row == None:
			cur.execute("CREATE TABLE users(user_id INTEGER PRIMARY KEY, aura INTEGER DEFAULT 0)")
			con.commit()

_init_data()
