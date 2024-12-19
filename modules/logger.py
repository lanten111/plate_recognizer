import logging

from modules.config import get_yaml_config


def setup_logger(name):
    """
    Sets up and returns a logger instance with console and file handlers.

    :param name: Name of the logger.
    :return: Configured logger instance.
    """
    # Create a logger
    config = get_yaml_config()
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)  # Set the global logging level

    # Avoid adding multiple handlers to the same logger
    if logger.hasHandlers():
        return logger

    # Create a console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)  # Set the console handler level

    # Create a file handler
    file_handler = logging.FileHandler(config.log_file_path, mode="a")
    file_handler.setLevel(logging.DEBUG)  # Set the file handler level

    # Create a formatter
    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

    # Add handlers to the logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger
