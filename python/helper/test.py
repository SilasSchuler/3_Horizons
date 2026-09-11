import os
import sys
import logging

logger = logging.getLogger("test")
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")

file_handler = logging.FileHandler("test.log", encoding="utf-8")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

logger.info(f"Called with arguments: {sys.argv[1:]}")

CONFIG_PATH_ENV = os.getenv("FRONTEND_CONFIG_PATH")
logger.info(f"Frontend config path: {CONFIG_PATH_ENV}")

print("Hello, World!")
logger.info("Hello, World!")