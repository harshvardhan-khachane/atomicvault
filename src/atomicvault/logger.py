import logging

# Configure the root logger exactly once
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# Export the central application logger
logger = logging.getLogger("atomicvault")
