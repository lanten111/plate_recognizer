import json
import sqlite3

from logger import setup_logger

logger = setup_logger(__name__)

def setup_db(config):
    conn = sqlite3.connect(config.db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS plates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            frigate_event_id TEXT NOT NULL UNIQUE,
            camera_name TEXT,       
            detected_plate TEXT,                 
            matched_watched_plate TEXT,    
            is_watched_plate_matched BOOLEAN,            
            watched_plates TEXT,                       
            is_trigger_zone_reached BOOLEAN,
            trigger_zones TEXT,
            entered_zones TEXT,
            vehicle_direction TEXT,               
            fuzzy_score TEXT,            
            vehicle_owner TEXT,
            vehicle_brand TEXT,
            image_path TEXT,
            detection_time TIMESTAMP,            
            created_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def execute_query(db_file, query, params=None):
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

def select_from_table(db_file, table, columns='*', where=None, params=None):
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
    query = f"SELECT {columns} FROM {table}"
    if where:
        query += f" WHERE {where}"

    return execute_query(db_file, query, params)

def insert_into_table(db_file, table, columns, values):
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
    return execute_query(db_file, query, values)

def update_table(db_file, table, set_clause, where, params):
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
    return execute_query(db_file, query, params)

def delete_from_table(db_file, table, where, params):
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
    return execute_query(db_file, query, params)



def create_or_update_plate(config, frigate_event_id, camera_name=None, detected_plate=None, matched_watched_plate=None, watched_plates = None,
                           detection_time=None, fuzzy_score=None, vehicle_direction=None,is_watched_plate_matched=None,
                           is_trigger_zone_reached=None, trigger_zones=None, entered_zones=None, image_path=None):
    logger.info(f"storing plate({detected_plate}) in db for event {frigate_event_id}")

    # Query to check if the record exists
    columns = '*'
    where = 'frigate_event_id = ?'
    params = (frigate_event_id,)
    results = select_from_table(config.db_path, config.table, columns, where, params)

    if results:
        # Update the record if it exists
        set_columns = {}
        if detection_time is not None: set_columns['detection_time'] = detection_time
        if fuzzy_score is not None: set_columns['fuzzy_score'] = fuzzy_score
        if detected_plate is not None: set_columns['detected_plate'] = detected_plate
        if camera_name is not None: set_columns['camera_name'] = camera_name
        if vehicle_direction is not None: set_columns['vehicle_direction'] = vehicle_direction
        if matched_watched_plate is not None: set_columns['matched_watched_plate'] = matched_watched_plate.number
        if is_watched_plate_matched is not None: set_columns['is_watched_plate_matched'] = is_watched_plate_matched
        if is_trigger_zone_reached is not None: set_columns['is_trigger_zone_reached'] = is_trigger_zone_reached
        if trigger_zones is not None: set_columns['trigger_zones'] = json.dumps(trigger_zones)
        if entered_zones is not None: set_columns['entered_zones'] = json.dumps(entered_zones)
        # if watched_plates is not None: set_columns['watched_plates'] = json.dumps(watched_plates)
        if matched_watched_plate and matched_watched_plate.owner is not None: set_columns['vehicle_owner'] = matched_watched_plate.owner
        if matched_watched_plate and matched_watched_plate.car_brand is not None: set_columns['vehicle_brand'] = matched_watched_plate.car_brand
        if image_path is not None: set_columns['image_path'] = image_path

        set_clause = ', '.join([f"{key} = ?" for key in set_columns.keys()])
        params = tuple(set_columns.values()) + (frigate_event_id,)

        where = 'frigate_event_id = ?'
        results = update_table(config.db_path, config.table, set_clause, where, params)
        logger.info(f"updated db for event {frigate_event_id}.")
    else:
        # Insert a new record if it doesn't exist
        insert_columns = []
        insert_values = []

        if detection_time is not None:
            insert_columns.append('detection_time')
            insert_values.append(detection_time)
        if fuzzy_score is not None:
            insert_columns.append('fuzzy_score')
            insert_values.append(fuzzy_score)
        if detected_plate is not None:
            insert_columns.append('detected_plate')
            insert_values.append(detected_plate)
        if vehicle_direction is not None:
            insert_columns.append('vehicle_direction')
            insert_values.append(vehicle_direction)
        if frigate_event_id is not None:
            insert_columns.append('frigate_event_id')
            insert_values.append(frigate_event_id)
        if camera_name is not None:
            insert_columns.append('camera_name')
            insert_values.append(camera_name)
        if matched_watched_plate is not None:
            insert_columns.append('matched_watched_plate')
            insert_values.append(matched_watched_plate.number)
        if watched_plates is not None:
            insert_columns.append('watched_plates')
            insert_values.append(json.dumps(config.watched_plates))
        if is_watched_plate_matched is not None:
            insert_columns.append('is_watched_plate_matched')
            insert_values.append(is_watched_plate_matched)
        if is_trigger_zone_reached is not None:
            insert_columns.append('trigger_zone_reached')
            insert_values.append(is_trigger_zone_reached)
        if trigger_zones is not None:
            insert_columns.append('trigger_zones')
            insert_values.append(json.dumps(trigger_zones))
        if entered_zones is not None:
            insert_columns.append('entered_zones')
            insert_values.append(json.dumps(entered_zones))
        if matched_watched_plate and matched_watched_plate.owner is not None:
            insert_columns.append('vehicle_owner')
            insert_values.append(matched_watched_plate.owner)
        if matched_watched_plate and matched_watched_plate.car_brand is not None:
            insert_columns.append('vehicle_brand')
            insert_values.append(matched_watched_plate.car_brand)
        if image_path is not None:
            insert_columns.append('image_path')
            insert_values.append(image_path)

        # Only insert the provided columns and values
        results = insert_into_table(config.db_path, config.table, insert_columns, insert_values)
        logger.info(f"inserted db for event {frigate_event_id}.")

    return results

def get_plate(config, frigate_event_id):
    try:
        logger.info(f"getting plate from db for event {frigate_event_id}.")
        columns = '*'
        where = 'frigate_event_id = ?'
        params = (frigate_event_id,)
        results = select_from_table(config.db_path , config.table, columns,  where, params)
        return results
    except sqlite3.Error as e:
        logger.error(f"SQLite error: {e}")
        result = None
