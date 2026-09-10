import logging
logger = logging.getLogger("test")
logger.setLevel(logging.INFO)
formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%H:%M:%S")

file_handler = logging.FileHandler("test.log", encoding="utf-8")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

print("Hello, World!")
logger.info("Hello, World!")