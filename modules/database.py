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


def execute_query(db_file, query, params=None, logger=None):
    """
    Executes a query (SELECT, INSERT, UPDATE, DELETE) on the SQLite database.

    :param logger:
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
        logger.error(f"SQLite error: {e}")
        result = None

    finally:
        # Ensure the connection is closed
        conn.close()

    return result

def select_from_table(db_file, table, columns='*', where=None, params=None, logger=None):
    """
    Executes a SELECT query on a specified table.

    :param logger:
    :param db_file: Path to the SQLite database file.
    :param table: Name of the table to query.
    :param columns: Columns to select (default is all columns '*').
    :param where: Optional WHERE condition for the query.
    :param params: Optional tuple of parameters to pass into the query.
    :return: List of dictionaries representing the rows returned by the query.
    """
    query = f"SELECT {columns} FROM {table}C"
    if where:
        query += f" WHERE {where} ORDER BY created_date  DESC"
    query += " ORDER BY created_date DESC"

    return execute_query(db_file, query, params, logger)

def insert_into_table(db_file, table, columns, values, logger):
    """
    Executes an INSERT query on a specified table.

    :param logger:
    :param db_file: Path to the SQLite database file.
    :param table: Name of the table to insert into.
    :param columns: A string or tuple of column names to insert into.
    :param values: A tuple of values to insert into the columns.
    :return: None
    """
    query = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['?'] * len(values))})"
    return execute_query(db_file, query, values, logger)

def update_table(db_file, table, set_clause, where, params, logger=None):
    """
    Executes an UPDATE query on a specified table.

    :param logger:
    :param db_file: Path to the SQLite database file.
    :param table: Name of the table to update.
    :param set_clause: The SET clause to update columns (e.g., 'column1 = ?, column2 = ?').
    :param where: The WHERE condition to specify which rows to update.
    :param params: Tuple of values to update the table.
    :return: None
    """
    query = f"UPDATE {table} SET {set_clause} WHERE {where}"
    return execute_query(db_file, query, params, logger)

def delete_from_table(db_file, table, where, params, logger):
    """
    Executes a DELETE query on a specified table.

    :param logger:
    :param db_file: Path to the SQLite database file.
    :param table: Name of the table to delete from.
    :param where: The WHERE condition to specify which rows to delete.
    :param params: Tuple of parameters to pass into the query.
    :return: None
    """
    query = f"DELETE FROM {table} WHERE {where}"
    return execute_query(db_file, query, params, logger)