import logging
from logging.handlers import RotatingFileHandler

# Define the logger configuration
def setup_logging():
    # Create a custom logger
    logger = logging.getLogger("RotBot")
    logger.setLevel(logging.DEBUG)

    # Create handlers
    console_handler = logging.StreamHandler()
    file_handler = RotatingFileHandler("rotbot.log", maxBytes=5 * 1024 * 1024, backupCount=2)

    # Set log levels
    console_handler.setLevel(logging.INFO)
    file_handler.setLevel(logging.DEBUG)

    # Create a formatter and add it to the handlers
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    console_handler.setFormatter(formatter)
    file_handler.setFormatter(formatter)

    # Add handlers to the logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    return logger


# Initialize the logger
logger = setup_logging()
