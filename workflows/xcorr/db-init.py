
# DB INIT PY
# Initialize the SQLite DB
# See init-db.sql for the table schema

import sqlite3
conn = sqlite3.connect('xcorr.db')
cursor = conn.cursor()

with open("init-db.sql", "r") as fp:
    sqlcode = fp.read()

cursor.execute(sqlcode);