import sqlite3


def setup_db(config):
    conn = sqlite3.connect(config.db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS plates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            detection_time TIMESTAMP,
            fuzzy_score TEXT,
            detected_plate_number TEXT,
            frigate_event_id TEXT NOT NULL UNIQUE,
            camera_name TEXT,
            watched_plate TEXT,
            plate_found BOOLEAN,
            vehicle_direction TEXT,    
            created_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def execute_query(db_file, query, params=None):
    """
    Executes a query (SELECT, INSERT, UPDATE, DELETE) on the SQLite database.

    :param db_file: Path to the SQLite database file.
    :param query: The SQL query to execute.
    :param params: Optional tuple of parameters to be used in the query (default is None).
    :return: Result for SELECT queries (list of dictionaries); None for INSERT, UPDATE, DELETE.
    """
    # Connect to the SQLite database
    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    # For returning dictionary results from SELECT queries
    cursor.row_factory = sqlite3.Row

    try:
        # Execute the query with parameters (if provided)
        cursor.execute(query, params or ())

        # Commit changes for INSERT, UPDATE, DELETE
        if query.strip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')):
            conn.commit()
            result = None
        else:  # For SELECT queries, fetch results as dictionaries
            result = [dict(row) for row in cursor.fetchall()]

    except sqlite3.Error as e:
        print(f"SQLite error: {e}")
        result = None

    finally:
        # Ensure the connection is closed
        conn.close()

    return result

def select_from_table(db_file, table, columns='*', where=None, params=None):
    """
    Executes a SELECT query on a specified table.

    :param db_file: Path to the SQLite database file.
    :param table: Name of the table to query.
    :param columns: Columns to select (default is all columns '*').
    :param where: Optional WHERE condition for the query.
    :param params: Optional tuple of parameters to pass into the query.
    :return: List of dictionaries representing the rows returned by the query.
    """
    query = f"SELECT {columns} FROM {table}"
    if where:
        query += f" WHERE {where}"

    return execute_query(db_file, query, params)

def insert_into_table(db_file, table, columns, values):
    """
    Executes an INSERT query on a specified table.

    :param db_file: Path to the SQLite database file.
    :param table: Name of the table to insert into.
    :param columns: A string or tuple of column names to insert into.
    :param values: A tuple of values to insert into the columns.
    :return: None
    """
    query = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(values))})"
    return execute_query(db_file, query, values)

def update_table(db_file, table, set_clause, where, params):
    """
    Executes an UPDATE query on a specified table.

    :param db_file: Path to the SQLite database file.
    :param table: Name of the table to update.
    :param set_clause: The SET clause to update columns (e.g., 'column1 = ?, column2 = ?').
    :param where: The WHERE condition to specify which rows to update.
    :param params: Tuple of values to update the table.
    :return: None
    """
    query = f"UPDATE {table} SET {set_clause} WHERE {where}"
    return execute_query(db_file, query, params)

def delete_from_table(db_file, table, where, params):
    """
    Executes a DELETE query on a specified table.

    :param db_file: Path to the SQLite database file.
    :param table: Name of the table to delete from.
    :param where: The WHERE condition to specify which rows to delete.
    :param params: Tuple of parameters to pass into the query.
    :return: None
    """
    query = f"DELETE FROM {table} WHERE {where}"
    return execute_query(db_file, query, params)



# def get_db_event_direction(frigate_event_id):
#     conn = sqlite3.connect(DB_PATH)
#     cursor = conn.cursor()
#     cursor.execute("""SELECT vehicle_direction FROM plates WHERE frigate_event_id = ?""", (frigate_event_id,))
#     row = cursor.fetchone()
#     conn.close()
#     return row


# def store_plate_in_db(detection_time, detected_plate_number, fuzzy_score, frigate_event_id, camera_name, watched_plate, plate_found ):
#     try:
#         conn = sqlite3.connect(DB_PATH)
#         cursor = conn.cursor()
#
#         _LOGGER.info(f"Storing plate number in database: {detected_plate_number} with score: {fuzzy_score}")
#
#         cursor.execute("""SELECT id FROM plates WHERE frigate_event_id = ?""", (frigate_event_id,))
#         event_id = cursor.fetchone()
#
#         if event_id:
#             cursor.execute("""
#                 UPDATE plates
#                 SET detection_time = ?,
#                     fuzzy_score = ?,
#                     detected_plate_number = ?,
#                     frigate_event_id = ?,
#                     camera_name = ?,
#                     watched_plate = ?,
#                     plate_found = ?
#                 WHERE id = ?
#             """, (detection_time, fuzzy_score, detected_plate_number, frigate_event_id, camera_name, watched_plate, plate_found, event_id[0]))
#
#         else:
#             cursor.execute("""INSERT INTO plates (detection_time, fuzzy_score , detected_plate_number, frigate_event_id , camera_name, watched_plate, plate_found  ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
#                            (detection_time, fuzzy_score, detected_plate_number, frigate_event_id, camera_name,watched_plate, plate_found))
#         conn.commit()
#     except sqlite3.Error as e:
#         _LOGGER.error(f"Database error: {e}")
#     finally:
#         conn.close()



# def is_plate_found_for_event(frigate_event_id):
#     conn = sqlite3.connect(DB_PATH)
#     cursor = conn.cursor()
#     cursor.execute("""SELECT plate_found FROM plates WHERE frigate_event_id = ?""", (frigate_event_id,))
#     result = cursor.fetchone()
#     conn.close()
#     return bool(result[0]) if result else None



# def store_vehicle_direction_in_db(vehicle_direction, frigate_event_id):
#     try:
#         conn = sqlite3.connect(DB_PATH)
#         cursor = conn.cursor()
#         _LOGGER.info(f"Storing vehicle direction number in database: {vehicle_direction} for event: {frigate_event_id}")
#         cursor.execute("""INSERT INTO plates (vehicle_direction, frigate_event_id) VALUES (?, ?)""",
#                        (vehicle_direction, frigate_event_id))
#         conn.commit()
#     except sqlite3.Error as e:
#         _LOGGER.error(f"Database error: {e}")
#     finally:
#         conn.close()

# def is_duplicate_event(frigate_event_id):
#     # see if we have already processed this event
#     conn = sqlite3.connect(DB_PATH)
#     cursor = conn.cursor()
#     cursor.execute("""SELECT * FROM plates WHERE frigate_event_id = ?""", (frigate_event_id,))
#     row = cursor.fetchone()
#     conn.close()
#
#     if row is not None:
#         _LOGGER.debug(f"Skipping event: {frigate_event_id} because it has already been processed")
#         return True
#
#     return False